# ===================== MILO bundle writer (contract 0.3, Perl) — do not edit =====================
# Builds a MILO sim bundle with INPUT and OUTPUT fully separated:
#   <bundle_id>/INPUT/inputs.json + INPUT/files/   <bundle_id>/OUTPUT/outputs.json + OUTPUT/files/
#   <bundle_id>/bundle.json  (written LAST; bundle_id, product, module, task, status, start, finish, time_elapsed,
#                             campaign when the run is part of one, redo_of when it redoes a failed run)
# A run in a campaign is delivered to MILO_DROP_DIR/<campaign folder>/<bundle_id>/.
# The bundle is staged in the work folder, then copied to MILO_DROP_DIR (bundle.json copied last).
# Core Perl modules only (Discovery Studio ships a full Perl), and no JSON module: values are written by hand
# so numbers stay numbers and names keep the order the script recorded them in.
package MiloBundle;
use strict;
use warnings;
use Carp ();
use Cwd ();
use File::Basename ();
use File::Copy ();
use File::Find ();
use File::Path ();
use File::Spec ();
use POSIX ();
use Scalar::Util ();
use Time::HiRes ();

our $CONTRACT = "0.3";
our $LAST_TRACE;  # stack of the most recent die, for record_error
$SIG{__DIE__} = sub { $LAST_TRACE = Carp::longmess("$_[0]") };

sub _now { POSIX::strftime("%Y-%m-%dT%H:%M:%SZ", gmtime()) }

# ---- JSON (json.dumps(value, indent=2) equivalent) ----
sub _jsonable {
    my ($value) = @_;
    return undef unless defined $value;
    my $kind = ref $value;
    if ($kind eq 'ARRAY') { return [map { _jsonable($_) } @$value] }
    if ($kind eq 'HASH') { return {map { ($_ => _jsonable($value->{$_})) } keys %$value} }
    if ($kind eq 'MiloBundle::Ordered' || $kind eq 'MiloBundle::String') { return $value }
    if ($kind) {
        # Discovery Studio arrays (StringArray, RealArray, Mdm::Array, ...) iterate as @$value: keep their items.
        my @items = eval { @$value };
        return [map { _jsonable($_) } @items] if !$@ && @items;
        return "$value";
    }
    return $value;
}

sub _json_string {
    my ($text) = @_;
    my %escape = ('"' => '\\"', "\\" => "\\\\", "\n" => "\\n", "\r" => "\\r", "\t" => "\\t", "\b" => "\\b", "\f" => "\\f");
    my $out = '';
    for my $ch (split //, $text) {
        my $code = ord $ch;
        if (exists $escape{$ch}) { $out .= $escape{$ch} }
        elsif ($code >= 0x20 && $code < 0x7F) { $out .= $ch }
        elsif ($code > 0xFFFF) {
            $code -= 0x10000;
            $out .= sprintf("\\u%04x\\u%04x", 0xD800 | ($code >> 10), 0xDC00 | ($code & 0x3FF));
        }
        else { $out .= sprintf("\\u%04x", $code) }
    }
    return '"' . $out . '"';
}

sub _json_scalar {
    my ($value) = @_;
    return "null" unless defined $value;
    return "true" if $value eq "True" || $value eq "true";
    return "false" if $value eq "False" || $value eq "false";
    if (Scalar::Util::looks_like_number($value) && $value =~ /^\s*-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$/) {
        my $number = $value + 0;
        return _json_string("$value") if $number != $number || $number * 0 != 0;  # NaN / Inf stay strings
        (my $text = $value) =~ s/^\s+|\s+$//g;
        $text =~ s/^(-?)0+(\d)/$1$2/;
        $text =~ s/\.$/.0/;
        $text = "0$text" if $text =~ /^\./;
        $text =~ s/^-\./-0./;
        return $text;
    }
    return _json_string("$value");
}

sub _json_text {
    my ($value, $level) = @_;
    $level ||= 0;
    my ($pad, $inner) = ("  " x $level, "  " x ($level + 1));
    my $kind = ref $value;
    if ($kind eq 'MiloBundle::Ordered') {
        return "{}" unless @{$value->{keys}};
        return "{\n" . join(",\n", map { $inner . _json_string($_) . ": " . _json_text($value->{values}{$_}, $level + 1) }
                                  @{$value->{keys}}) . "\n$pad}";
    }
    return _json_string($$value) if $kind eq 'MiloBundle::String';
    $value = _jsonable($value);
    $kind = ref $value;
    if ($kind eq 'ARRAY') {
        return "[]" unless @$value;
        return "[\n" . join(",\n", map { $inner . _json_text($_, $level + 1) } @$value) . "\n$pad]";
    }
    if ($kind eq 'HASH') {
        return "{}" unless %$value;
        return "{\n" . join(",\n", map { $inner . _json_string($_) . ": " . _json_text($value->{$_}, $level + 1) }
                                  sort keys %$value) . "\n$pad}";
    }
    return _json_scalar($value);
}

# An insertion-ordered map, so inputs.json/outputs.json list names in the order the script recorded them.
package MiloBundle::Ordered;
sub new { bless {keys => [], values => {}}, shift }
sub set {
    my ($self, $key, $value) = @_;
    push @{$self->{keys}}, $key unless exists $self->{values}{$key};
    $self->{values}{$key} = $value;
}
package MiloBundle;

# MiloBundle::text($value): always written as a JSON string, even when it looks like a number ("0.1", "007").
sub text { my $value = shift; return defined $value ? bless(\"$value", 'MiloBundle::String') : undef }

sub new {
    my ($class, %args) = @_;
    my $self = bless {
        bundle_id => $args{bundle_id},
        drop_dir => $args{drop_dir},
        work_dir => File::Spec->rel2abs($args{work_dir} || Cwd::getcwd()),
        inputs => MiloBundle::Ordered->new,
        outputs => MiloBundle::Ordered->new,
        time_elapsed => undef,
    }, $class;
    $self->{root} = File::Spec->catdir($self->{work_dir}, "milo_bundle_" . $self->{bundle_id});
    $self->{manifest} = MiloBundle::Ordered->new;
    my @manifest = (
        bundle_id => text($args{bundle_id}), title => text($args{title} || $args{task}), product => text($args{product}),
        module => text($args{module}), task => text($args{task}), status => "running", start => _now(), finish => undef,
        time_elapsed => undef, time_elapsed_units => "s", milo_contract => text($CONTRACT),
    );
    while (my ($key, $value) = splice(@manifest, 0, 2)) { $self->{manifest}->set($key, $value) }
    # A campaign is a named series of similar runs; a run that is not part of one carries no campaign field.
    if (defined $args{campaign} && $args{campaign} =~ /\S/) {
        (my $campaign = $args{campaign}) =~ s/^\s+|\s+$//g;
        $self->{manifest}->set(campaign => text($campaign));
        $self->{campaign} = $campaign;
    }
    # A run made again because an earlier one failed names that run, so MILO links the two.
    if (defined $args{redo_of} && $args{redo_of} =~ /\S/) {
        (my $redo_of = $args{redo_of}) =~ s/^\s+|\s+$//g;
        $self->{manifest}->set(redo_of => text($redo_of));
    }
    File::Path::make_path(File::Spec->catdir($self->{root}, $_, "files")) for qw(INPUT OUTPUT);
    return $self;
}

sub root { $_[0]{root} }

# ---- values ----
sub _item {
    my ($value, $units, %meta) = @_;
    my $item = MiloBundle::Ordered->new;
    $item->set(value => _jsonable($value));
    $item->set(units => $units);
    $item->set($_ => _jsonable($meta{$_})) for sort keys %meta;
    return $item;
}

sub input { my ($self, $name, $value, $units, %meta) = @_; $self->{inputs}->set($name, _item($value, $units, %meta)) }
sub output { my ($self, $name, $value, $units, %meta) = @_; $self->{outputs}->set($name, _item($value, $units, %meta)) }

# ---- files ----
sub input_path { my ($self, $name) = @_; return File::Spec->catfile($self->{root}, "INPUT", "files", $name) }
sub output_path { my ($self, $name) = @_; return File::Spec->catfile($self->{root}, "OUTPUT", "files", $name) }

sub _copy_in {
    my ($self, $sector, $path, $name) = @_;
    my $dest = File::Spec->catfile($self->{root}, $sector, "files", $name // File::Basename::basename($path));
    File::Path::make_path(File::Basename::dirname($dest));
    File::Copy::copy($path, $dest) or die "MILO: could not copy $path to $dest: $!\n";
    return $dest;
}

sub input_file { my ($self, $path, $name) = @_; return $self->_copy_in("INPUT", $path, $name) }
sub output_file { my ($self, $path, $name) = @_; return $self->_copy_in("OUTPUT", $path, $name) }

sub write_text {
    my ($self, $sector, $name, $text) = @_;
    my $path = File::Spec->catfile($self->{root}, $sector, "files", $name);
    File::Path::make_path(File::Basename::dirname($path));
    open(my $handle, ">:encoding(UTF-8)", $path) or die "MILO: cannot write $path: $!\n";
    print $handle $text;
    close $handle;
    return $path;
}

# Copy a whole folder (e.g. a protocol's RunPath) into <sector>/files/<subfolder>/: every file the job wrote.
sub capture_tree {
    my ($self, $folder, $sector, $subfolder) = @_;
    my @copied;
    return \@copied unless defined $folder && -d $folder;
    my $base = File::Spec->rel2abs($folder);
    File::Find::find({no_chdir => 1, wanted => sub {
        return unless -f $File::Find::name;
        my $rel = File::Spec->abs2rel(File::Spec->rel2abs($File::Find::name), $base);
        $rel =~ s{\\}{/}g;
        my $dest = defined $subfolder && length $subfolder ? "$subfolder/$rel" : $rel;
        if (eval { $self->_copy_in($sector, $File::Find::name, $dest); 1 }) { push @copied, $rel }
        else {
            my $full = File::Spec->catfile($self->{root}, $sector, "files", $dest);
            printf "MILO: could not copy %s (%d chars) to %s (%d chars): %s", $File::Find::name,
                length $File::Find::name, $full, length $full, $@;
        }
    }}, $base);
    return [sort @copied];
}

# ---- timing ----
# $bundle->timed(sub { <the BIOVIA calculation> })  -> sets time_elapsed (seconds); errors still propagate.
sub timed {
    my ($self, $code) = @_;
    my $t0 = Time::HiRes::time();
    my @result = eval { $code->() };
    my $error = $@;
    $self->{time_elapsed} = Time::HiRes::time() - $t0;
    die $error if $error;
    return wantarray ? @result : $result[0];
}

# ---- Discovery Studio helpers: same for every protocol ----
sub safe {
    my ($self, $obj, $attr, @args) = @_;
    return undef unless defined $obj && ref $obj;
    my $value = eval { $obj->$attr(@args) };
    return $@ ? undef : $value;
}

sub _prefixed { my ($stage, $name) = @_; return defined $stage && length $stage ? "$stage.$name" : $name }

# Apply parameters to a Protocol::Document, record each one as an INPUT, and dump the protocol's FULL
# parameter set (every default the run used, not only the ones changed) into INPUT/files/settings/.
# parameters: [[name, value, units], ...]. An unknown parameter name dies: a silently ignored typo
# would mean the bundle claims an input the run never used. The protocol's own parameters (types and
# defaults) are dumped first, so a run that stops on a refused setting still shows what it accepts.
sub apply_parameters {
    my ($self, $protocol, $stage, $parameters) = @_;
    my $label = defined $stage && length $stage ? $stage : "run";
    my $folder = defined $stage && length $stage ? "settings/$stage" : "settings";
    my @names = $self->_dump_parameters($protocol, "$folder/milo_parameters_${label}_defaults.tsv");
    for my $parameter (@$parameters) {
        my ($name, $value, $units) = @$parameter;
        die "MILO: protocol has no parameter '$name'\n" unless $protocol->ItemExists($name);
        my $given = $self->_as_parameter_type($protocol, $name, $value);
        eval { $protocol->ReplaceItem($name, $given); 1 } or do {
            my ($error, $type, $default) = ($@, $self->safe($protocol, "ParameterType", $name), $self->safe($protocol, "Item", $name));
            $error =~ s/\s+at \S+ line \d+.*//s;
            die "MILO: Discovery Studio refused protocol parameter '$name' = '$value' (its type: "
                . ($type // "unknown") . ", its default: " . ($default // "unknown") . "): "
                . (length $error ? $error : "no message") . "\n";
        };
        $self->input(_prefixed($stage, $name), $value, $units);
    }
    push @names, $self->_dump_parameters($protocol, "$folder/milo_parameters_$label.tsv");
    my $xml = $self->input_path("$folder/milo_protocol_$label.xml");
    File::Path::make_path(File::Basename::dirname($xml));
    push @names, "$folder/milo_protocol_$label.xml" if eval { $protocol->Save($xml, "pr_xml"); -f $xml };
    $self->input(_prefixed($stage, "settings_dump"), \@names);
}

# A value as the protocol parameter's own type (ParameterType): Discovery Studio refuses the words
# "True"/"False" for a BoolType (it takes its True/False constants) and a Perl integer such as 7 for a
# DoubleType (it takes a real number), both seen on the VM with no message.
sub _as_parameter_type {
    my ($self, $protocol, $name, $value) = @_;
    return $value if !defined $value || ref $value;
    my $type = $self->safe($protocol, "ParameterType", $name) // "";
    my $number = Scalar::Util::looks_like_number($value);
    if ($type =~ /^Bool/i || $value =~ /^(true|false)$/i) {
        my $on = $number ? $value != 0 : lc $value eq "true";
        return $on ? main::True() : main::False() if $number || $value =~ /^(true|false)$/i;
    }
    return unpack("d", pack("d", $value)) if $type =~ /^Double/i && $number;  # a real number, never an integer
    return int($value) if $type =~ /^Long/i && $number && $value == int($value);
    return $value;
}

# Every parameter of a protocol as it stands (name, type, value) into INPUT/files/<rel>; returns [rel] or ().
sub _dump_parameters {
    my ($self, $protocol, $rel) = @_;
    my @dump;
    my $map = $self->safe($protocol, "ParameterMap");
    my $key = $self->safe($map, "FirstKey");
    while (defined $key && length $key) {
        my $value = $self->safe($map, "Item", $key);
        my $type = $self->safe($protocol, "ParameterType", $key);
        push @dump, "$key\t" . (defined $type ? $type : "") . "\t" . (defined $value ? "$value" : "");
        $key = $self->safe($map, "NextKey", $key);
    }
    return () unless @dump;
    $self->write_text("INPUT", $rel, join("\n", "name\ttype\tvalue", @dump) . "\n");
    return ($rel);
}

# Record every property Discovery Studio defines on an object (PropertyNames), skipping ones it can't read.
sub record_properties {
    my ($self, $stage, $obj, $properties, $units, $sector) = @_;
    $units ||= {};
    $properties ||= $self->safe($obj, "PropertyNames") || [];
    my $method = ($sector || "OUTPUT") eq "INPUT" ? "input" : "output";
    my @names = eval { @$properties };
    for my $prop (@names) {
        my $value = $self->safe($obj, "GetProperty", $prop);
        $self->$method(_prefixed($stage, $prop), $value, $units->{$prop}) if defined $value && "$value" ne "";
    }
}

# Every property of every molecule in an Mdm::Document (energies in kcal/mol, counts, formula, ...).
# One molecule: "<stage>.<property>"; several: "<stage>.<molecule name>.<property>".
sub record_molecules {
    my ($self, $stage, $document, $sector) = @_;
    my $molecules = $self->safe($document, "Molecules");
    my @molecules = $molecules ? eval { @$molecules } : ();
    my $prefix = defined $stage && length $stage ? "$stage." : "";
    $self->output("${prefix}molecule_count", scalar @molecules) unless ($sector || "OUTPUT") eq "INPUT";
    my $index = 0;
    for my $molecule (@molecules) {
        my $name = $self->safe($molecule, "Name");
        $name = "molecule" . ++$index unless defined $name && length $name;
        my $label = @molecules == 1 ? $stage : "$prefix$name";
        my $names = $self->safe($molecule, "PropertyNames");
        my %units = map { ($_ => "kcal/mol") } grep { /Energy/i } ($names ? eval { @$names } : ());
        $self->record_properties($label, $molecule, $names, \%units, $sector);
    }
}

# What a file in a protocol's run folder is, from where it sits: Output/ holds the protocol's final results
# (FINAL), Intermediate/ the steps on the way (intermediate.<path>), Input/ the input as the server received
# it (protocol_input.<path>). A final file that is the only one of its kind (the only structure, the only
# .pK table) is plain FINAL; several of a kind are FINAL.<name>. Two files that differ only by extension
# (1SVN_clean.dsv and 1SVN_clean.mol2) keep it, so neither overwrites the other.
sub result_label {
    my ($self, $rel, $files) = @_;
    (my $path = $rel) =~ s{\\}{/}g;
    my $kind = _result_kind($path);
    (my $stem = $path) =~ s{\.(\w+)$}{};
    my $ext = $1 // "";
    my $twins = grep { (my $other = $_) =~ s{\\}{/}g; $other =~ s{\.\w+$}{}; $other eq $stem } @$files;
    my $name = $twins > 1 ? "$stem.$ext" : $stem;
    return "intermediate.$1" if $name =~ m{^Intermediate/(.+)$}i;
    return "protocol_input.$1" if $name =~ m{^Input/(.+)$}i;
    return "run.$name" unless $name =~ m{^Output/}i;
    my $alike = grep { m{^Output[/\\]}i && _result_kind($_) eq $kind } @$files;
    return $alike == 1 ? "FINAL" : "FINAL." . File::Basename::basename($name);
}
sub _result_kind { my ($ext) = $_[0] =~ /\.(\w+)$/; $ext = lc($ext // ""); return $ext =~ /^(dsv|msv|sd|sdf|mol|mol2|pdb)$/ ? "structure" : $ext }

# A "name value" table with one header line (Discovery Studio .pK: "Residue  Calculated pK", then one
# residue per line): each row becomes <label>.<name>. Returns how many rows were recorded.
sub record_name_value_table {
    my ($self, $label, $path) = @_;
    open(my $fh, "<", $path) or return 0;
    my (@rows, $header);
    while (my $line = <$fh>) {
        $line =~ s/^\s+|\s+$//g;
        next unless length $line;
        next if !$header++;  # the header line
        my ($name, $value) = split /\s+/, $line, 2;
        return 0 unless defined $value && Scalar::Util::looks_like_number($value);  # not such a table: keep it as a file
        push @rows, [$name, $value + 0];
    }
    $self->output("$label.$_->[0]", $_->[1]) for @rows;
    return scalar @rows;
}

# A comma-separated table with a header row (Prepare Proteins' pH curve: pH, Total Charge, energies, the
# protonated fraction of every residue): each column becomes <label>.<column>, holding the whole column.
# Anything that is not a rectangular table is left as a file. Returns how many columns were recorded.
sub record_csv_table {
    my ($self, $label, $path) = @_;
    open(my $fh, "<", $path) or return 0;
    my $head = <$fh>;
    return 0 unless defined $head;
    $head =~ s/^\x{FEFF}|^\xEF\xBB\xBF//;
    my @names = map { s/^\s+|\s+$//gr } split /,/, $head =~ s/[\r\n]+$//r;
    return 0 unless @names > 1 && !grep { !length } @names;
    my @columns = map { [] } @names;
    while (my $line = <$fh>) {
        $line =~ s/[\r\n]+$//;
        next unless length $line;
        my @cells = map { s/^\s+|\s+$//gr } split /,/, $line, -1;
        return 0 unless @cells == @names;
        push @{$columns[$_]}, (Scalar::Util::looks_like_number($cells[$_]) ? $cells[$_] + 0 : $cells[$_]) for 0 .. $#names;
    }
    return 0 unless @{$columns[0]};
    $self->output("$label.$names[$_]", $columns[$_]) for 0 .. $#names;
    return scalar @names;
}

# A protocol task's final state, its log, and every file in its RunPath.
sub record_task {
    my ($self, $stage, $task) = @_;
    for my $prop (qw(ProtocolName TaskId Host State ProtocolStatus IsProtocolSuccessful RunPath)) {
        my $value = $self->safe($task, $prop);
        $self->output(_prefixed($stage, $prop), $value) if defined $value;
    }
    my $label = defined $stage && length $stage ? $stage : "run";
    my $log = $self->safe($task, "LogMessages");
    my @lines = $log ? eval { @$log } : ();
    $self->write_text("OUTPUT", "${label}_log.txt", join("\n", @lines) . "\n") if @lines;
    my $files = $self->capture_tree($self->safe($task, "RunPath"), "OUTPUT", defined $stage && length $stage ? "run/$stage" : "run");
    $self->output(_prefixed($stage, "run_files"), $files);
    return $files;
}

# ---- molecules from MILO's molecule memory ----
sub checksum {  # FNV-1a (32-bit) over the characters: the same as MILO's molecule memory computes
    my $text = shift;
    my $h = 0x811C9DC5;
    for my $ch (split //, $text) {
        $h ^= ord($ch);
        { use integer; $h = ($h * 16777619) & 0xFFFFFFFF; }
    }
    return sprintf("fnv1a:%08x", $h);
}

# Write a molecule from MILO's molecule memory (%MILO_MOLECULES, put in this script by the MCP) into
# INPUT/files/molecules/ exactly as it was saved, and record what it is. Its checksum is checked first:
# a molecule that changed on its way here stops the run. Returns the file's path, ready for Open.
sub molecule_file {
    my ($self, $name, $molecules) = @_;
    my $entry = $molecules->{$name} or die "molecule '$name' is not in this script's MILO_MOLECULES\n";
    my $found = checksum($entry->{structure});
    die "molecule '$name' changed on its way here (checksum $found, saved as $entry->{checksum}): "
        . "get a fresh script from the MILO MCP\n" unless $found eq $entry->{checksum};
    my $path = $self->input_path("molecules/$entry->{file_name}.$entry->{format}");
    File::Path::make_path(File::Basename::dirname($path));
    open(my $handle, ">:raw", $path) or die "cannot write $path: $!\n";
    print $handle $entry->{structure};
    close($handle);
    for my $key (qw(smiles canonical_smiles formula inchikey checksum)) {
        $self->input("molecule.$name.$key", text($entry->{$key})) if defined $entry->{$key} && length $entry->{$key};
    }
    return $path;
}

# ---- errors ----
sub record_error {
    my ($self, $error) = @_;
    (my $message = "$error") =~ s/\s+$//;
    $self->output("error", $message);
    $self->output("traceback", defined $LAST_TRACE ? $LAST_TRACE : $message);
}

# ---- finish ----
sub finish {
    my ($self, $status) = @_;
    $self->{manifest}->set(status => text($status));
    $self->{manifest}->set(finish => _now());
    $self->{manifest}->set(time_elapsed => $self->{time_elapsed});
    $self->_dump(File::Spec->catfile($self->{root}, "INPUT", "inputs.json"), $self->{inputs});
    $self->_dump(File::Spec->catfile($self->{root}, "OUTPUT", "outputs.json"), $self->{outputs});
    $self->_dump(File::Spec->catfile($self->{root}, "bundle.json"), $self->{manifest});  # last
    print "MILO: bundle $self->{bundle_id} staged at $self->{root} (status: $status)\n";
    $self->_deliver;
    return $self->{root};
}

sub _dump {
    my ($self, $path, $data) = @_;
    open(my $handle, ">:encoding(UTF-8)", $path) or die "MILO: cannot write $path: $!\n";
    print $handle _json_text($data);
    close $handle;
}

# The campaign's folder in the drop folder: characters Windows refuses in a folder name become "-"
# (the same rule as the MILO app's).
sub campaign_folder {
    (my $folder = shift) =~ s{[<>:"/\\|?*\x00-\x1f]}{-}g;
    $folder =~ s/^\s+//;
    $folder =~ s/[\s.]+$//;
    return length $folder ? $folder : "campaign";
}

sub _deliver {
    my ($self) = @_;
    unless ($self->{drop_dir}) {
        print "MILO: no drop folder set; bundle stays in the work folder.\n";
        return;
    }
    my $folder = defined $self->{campaign}
        ? File::Spec->catdir($self->{drop_dir}, campaign_folder($self->{campaign})) : $self->{drop_dir};
    my $dest = File::Spec->catdir($folder, $self->{bundle_id});
    my $ok = eval {
        my $root = $self->{root};
        File::Find::find({no_chdir => 1, wanted => sub {
            return unless -f $File::Find::name;
            my $rel = File::Spec->abs2rel($File::Find::name, $root);
            return if $rel eq "bundle.json";
            my $target = File::Spec->catfile($dest, $rel);
            File::Path::make_path(File::Basename::dirname($target));
            File::Copy::copy($File::Find::name, $target) or die "copy $rel: $!\n";
        }}, $root);
        File::Copy::copy(File::Spec->catfile($root, "bundle.json"), File::Spec->catfile($dest, "bundle.json"))
            or die "copy bundle.json: $!\n";
        1;
    };
    if ($ok) { print "MILO: bundle delivered to $dest\n" }
    else {
        print "MILO: could NOT copy bundle to drop folder $self->{drop_dir}: $@";
        print "MILO: the bundle is still in the work folder; copy it to the drop folder by hand.\n";
    }
}

package main;
# =================================== end MILO bundle writer ===================================
