# Stand-in for Discovery Studio's DiscoveryScript Perl API, used by milo_check_script (and the tests).
#
# It does NOT simulate anything; it only mimics the API surface MILO scripts touch (documents, molecules,
# forcefield typing, protocol documents/sessions/tasks), so script logic and bundle writing can be tested
# without BIOVIA. Any protocol name is accepted: known ones (Minimization, Prepare Proteins) have their
# parameter list and refuse wrongly typed values the way the real one does; unknown ones accept any
# parameter. Any other *DiscoveryScript / *Commands module "loads" as this one.
package DiscoveryScript;
use strict;
use warnings;
use B ();
use Cwd ();
use File::Basename ();
use File::Path ();
use File::Spec ();

our $SERVER_DOWN = $ENV{MILO_FAKE_DS_SERVER_DOWN} ? 1 : 0;  # tests set this to prove a failed run still writes a bundle

# Any DS module this stand-in doesn't define becomes an alias of it (the real API has dozens).
unshift @INC, sub {
    my (undef, $file) = @_;
    return unless $file =~ /^(\w+)(DiscoveryScript|Commands)\.pm$/;
    my $source = "package $1$2; use DiscoveryScript; sub import { DiscoveryScript::export_to(scalar caller) } 1;\n";
    open(my $fh, "<", \$source) or return;
    return $fh;
};

sub True () { 1 }
sub False () { 0 }
sub PdbUrlPrefix () { "https://files.rcsb.org/download/" }

sub export_to {
    my ($target) = @_;
    no strict 'refs';
    *{"${target}::$_"} = \&{"DiscoveryScript::$_"} for qw(True False PdbUrlPrefix);
    *{"${target}::LaunchProtocol"} = \&Protocol::LaunchProtocol;
}

sub import { export_to(scalar caller) }

sub GetTemporaryFolder { return Cwd::getcwd() }
sub IsStandalone { return 1 }

sub Open {
    my ($args) = @_;
    my $path = ref $args ? $args->{Path} : $args;
    die "DiscoveryScript::Open: no such file '$path'\n" unless -f $path;
    return Mdm::Document::_load($path);
}

sub OpenFromUrl {
    my ($url) = @_;
    my $doc = Mdm::Document::Create();
    my $molecule = $doc->CreateMolecule;
    $molecule->{props}{Name} = File::Basename::basename($url);
    $molecule->CreateAtom($_) for qw(N C C O);
    return $doc;
}

sub _write {
    my ($path, $text) = @_;
    File::Path::make_path(File::Basename::dirname($path));
    open(my $fh, ">", $path) or die "cannot write $path: $!\n";
    print $fh $text;
    close $fh;
}

# ---------------------------------------------------------------- arrays (@$array, ->Count, ->Item)
package DiscoveryScript::FakeArray;
sub new { my ($class, @items) = @_; return bless [@items], $class }
sub Count { scalar @{$_[0]} }
sub Item { $_[0][$_[1]] }
sub IsEmpty { !@{$_[0]} }
sub AddItem { push @{$_[0]}, $_[1] }

# ---------------------------------------------------------------- generic stub: anything not faked yet
package DiscoveryScript::Stub;
our $AUTOLOAD;
sub new { my ($class, $name) = @_; return bless {name => $name}, $class }
sub AUTOLOAD {
    my $self = shift;
    (my $method = $AUTOLOAD) =~ s/.*:://;
    return if $method eq "DESTROY";
    return DiscoveryScript::Stub->new("$self->{name}.$method");
}

# ---------------------------------------------------------------- Mdm (molecules)
package Mdm;
sub singleBond () { 1 }
sub doubleBond () { 2 }
sub tripleBond () { 3 }

package Mdm::Point;
sub Create { my ($x, $y, $z) = @_; return bless {X => $x, Y => $y, Z => $z}, "Mdm::Point" }
sub X { $_[0]{X} }
sub Y { $_[0]{Y} }
sub Z { $_[0]{Z} }

package Mdm::Atom;
sub new { my ($class, $element) = @_; return bless {props => {Name => $element, Element => $element}, xyz => [0, 0, 0]}, $class }
sub Name { $_[0]{props}{Name} }
sub ElementSymbol { $_[0]{props}{Element} }
sub XYZ { Mdm::Point::Create(@{$_[0]{xyz}}) }  # the real API: $atom->XYZ->X (Mdm::Point class docs)

# a residue of a protein read from a PDB file: Name "SER221", Id 221, Abbreviation "SER", its atoms
package Mdm::AminoAcid;
sub Name { $_[0]{name} }
sub Id { $_[0]{id} }
sub Abbreviation { $_[0]{abbreviation} }
sub Atoms { DiscoveryScript::FakeArray->new(@{$_[0]{atoms}}) }
sub GetProperty { $_[0]{props}{$_[1]} }
sub SetProperty { $_[0]{props}{$_[1]} = $_[2] }
sub PropertyNames { DiscoveryScript::FakeArray->new(sort keys %{$_[0]{props}}) }

package Mdm::Molecule;
our $AUTOLOAD;
sub new { my ($class, $name) = @_; return bless {atoms => [], props => {Name => $name}}, $class }
sub Name { my $self = shift; $self->{props}{Name} = shift if @_; return $self->{props}{Name} }
sub Atoms { DiscoveryScript::FakeArray->new(@{$_[0]{atoms}}) }
sub CreateAtom { my ($self, $element) = @_; my $atom = Mdm::Atom->new($element); push @{$self->{atoms}}, $atom; $self->_count; return $atom }
sub _count {
    my ($self) = @_;
    my %n;
    $n{$_->{props}{Element}}++ for @{$self->{atoms}};
    $self->{props}{"Number of Atoms"} = scalar @{$self->{atoms}};
    $self->{props}{Formula} = join(" ", map { "$_$n{$_}" } sort keys %n);
}
sub GetProperty { $_[0]{props}{$_[1]} }
sub SetProperty { $_[0]{props}{$_[1]} = $_[2] }
sub PropertyNames { DiscoveryScript::FakeArray->new(sort keys %{$_[0]{props}}) }
sub AUTOLOAD { my $self = shift; (my $m = $AUTOLOAD) =~ s/.*:://; return if $m eq "DESTROY"; DiscoveryScript::Stub->new("Molecule.$m") }

package Mdm::Document;
our $AUTOLOAD;
my %SMILES_ELEMENTS = (C => "C", c => "C", N => "N", n => "N", O => "O", o => "O", S => "S", s => "S", F => "F", P => "P");

sub Create { return bless {molecules => [], props => {}}, "Mdm::Document" }
sub CreateFromPdbId { my ($id) = @_; return DiscoveryScript::OpenFromUrl("$id.pdb") }
sub Molecules { DiscoveryScript::FakeArray->new(@{$_[0]{molecules}}) }
sub Atoms { DiscoveryScript::FakeArray->new(map { @{$_->{atoms}} } @{$_[0]{molecules}}) }
sub AminoAcids {
    my ($self) = @_;
    my (@residues, %seen);
    for my $atom (map { @{$_->{atoms}} } @{$self->{molecules}}) {
        my $residue = $atom->{residue} or next;
        my ($abbreviation, $id) = @$residue;
        my $key = "$abbreviation$id";
        push @residues, $seen{$key} = bless({name => $key, id => $id, abbreviation => $abbreviation, atoms => []},
                                            "Mdm::AminoAcid") unless $seen{$key};
        push @{$seen{$key}{atoms}}, $atom;
    }
    return DiscoveryScript::FakeArray->new(@residues);
}
sub CreateMolecule {
    my ($self) = @_;
    my $molecule = Mdm::Molecule->new("Molecule" . (@{$self->{molecules}} + 1));
    push @{$self->{molecules}}, $molecule;
    return $molecule;
}
sub CreateBond { return bless {}, "Mdm::Bond" }
sub InsertFromSmiles {
    my ($self, $smiles) = @_;
    $smiles = $smiles->{SmilesString} if ref $smiles;
    my $molecule = $self->CreateMolecule;
    $molecule->CreateAtom($SMILES_ELEMENTS{$_}) for grep { exists $SMILES_ELEMENTS{$_} } split //, $smiles;
    return $molecule;
}
sub AddHydrogenAtoms {
    my ($self) = @_;
    for my $molecule (@{$self->{molecules}}) {
        next if grep { $_->{props}{Element} eq "H" } @{$molecule->{atoms}};
        $molecule->CreateAtom("H") for 1 .. 2 * @{$molecule->{atoms}} + 2;
    }
}
sub Clean { 1 }
sub Save {
    my ($self, $path, $format) = @_;
    ($path, $format) = @{$path}{qw(Path FormatType)} if ref $path;
    my @lines = ("FAKE-MDM $format");
    for my $molecule (@{$self->{molecules}}) {
        # ATOM element, forcefield type, x y z, atom name, residue (abbreviation id): coordinates survive a save
        push @lines, "MOLECULE", map({ join("\t", "ATOM", $_->{props}{Element}, $_->{props}{ForcefieldType} // "",
                                            "@{$_->{xyz}}", $_->{props}{Name}, $_->{residue} ? "@{$_->{residue}}" : "") }
                                     @{$molecule->{atoms}}),
            map({ "PROP\t$_\t$molecule->{props}{$_}" } sort keys %{$molecule->{props}});
    }
    DiscoveryScript::_write($path, join("\n", @lines) . "\n");
}
sub _load {
    my ($path) = @_;
    my $doc = Create();
    open(my $fh, "<", $path) or die "cannot read $path: $!\n";
    my @lines = <$fh>;
    close $fh;
    # A real structure file (as molecule_file writes them from MILO's molecule memory): one molecule with one
    # atom per atom record, so scripts that open a PDB/MOL file see its atoms, as they would in Discovery Studio.
    my @pdb_atoms = grep { /^(ATOM  |HETATM)/ } @lines;
    if (@pdb_atoms || (@lines > 3 && $lines[3] =~ /V2000/)) {
        my $molecule = $doc->CreateMolecule;
        $molecule->{props}{Name} = File::Basename::basename($path) =~ s/\.\w+$//r;
        if (@pdb_atoms) {
            for my $record (@pdb_atoms) {
                my $element = length($record) >= 78 ? substr($record, 76, 2) : "";
                $element =~ s/\s+//g;
                ($element = substr($record, 12, 2)) =~ s/[\s\d]//g unless length $element;
                my $atom = $molecule->CreateAtom(ucfirst lc $element);
                my ($name, $residue, $number) = map { (my $v = $_) =~ s/\s+//g; $v }
                                                substr($record, 12, 4), substr($record, 17, 3), substr($record, 22, 4);
                $atom->{props}{Name} = $name;
                $atom->{xyz} = [map { substr($record, $_, 8) + 0 } 30, 38, 46];
                $atom->{residue} = [$residue, $number] if $record =~ /^ATOM/;
            }
        }
        else {
            my $count = substr($lines[3], 0, 3) + 0;  # MOL V2000 counts line: atoms in columns 1-3 ("168176" = 168 atoms, 176 bonds)
            for (0 .. $count - 1) {
                my ($x, $y, $z, $element) = split ' ', $lines[4 + $_];
                $molecule->CreateAtom($element)->{xyz} = [$x + 0, $y + 0, $z + 0];
            }
        }
        return $doc;
    }
    my $molecule;
    for my $line (@lines) {
        chomp $line;
        my @f = split /\t/, $line;
        if ($f[0] eq "MOLECULE") { $molecule = $doc->CreateMolecule }
        elsif ($f[0] eq "ATOM" && $molecule) {
            my $a = $molecule->CreateAtom($f[1]);
            $a->{props}{ForcefieldType} = $f[2] if $f[2];
            $a->{xyz} = [split ' ', $f[3]] if $f[3];
            $a->{props}{Name} = $f[4] if $f[4];
            $a->{residue} = [split ' ', $f[5]] if $f[5];
        }
        elsif ($f[0] eq "PROP" && $molecule) { $molecule->{props}{$f[1]} = $f[2] }
    }
    return $doc;
}
sub Close { 1 }
sub AUTOLOAD { my $self = shift; (my $m = $AUTOLOAD) =~ s/.*:://; return if $m eq "DESTROY"; DiscoveryScript::Stub->new("Document.$m") }

# ---------------------------------------------------------------- Ffdm (forcefields)
package Ffdm;
sub charmmForceField () { "CHARMm" }
sub charmm19ForceField () { "charmm19" }
sub charmm22ForceField () { "charmm22" }
sub charmm27ForceField () { "charmm27" }
sub charmm36ForceField () { "charmm36" }
sub charmmPolarHydrogenForceField () { "CHARMm Polar H" }
sub cffForceField () { "cff" }
sub mmffForceField () { "MMFF" }
sub xproligForceField () { "xprolig" }
sub applyForceFieldCompleted () { 0 }
sub applyForceFieldFailed () { 1 }
sub useCurrentPartialCharges () { "Current" }
sub useMmff94PartialCharges () { "MMFF94" }
sub useMomanyRonePartialCharges () { "Momany-Rone" }

package Ffdm::Document;
sub Create { my ($type) = @_; $type = $type->{ForceFieldType} if ref $type; return bless {type => $type}, "Ffdm::Document" }
sub ApplyForceField {
    my ($self, $doc, undef, undef, undef, $charges) = @_;
    ($doc, $charges) = @{$doc}{qw(MdmDocument PartialChargeMethod)} if ref $doc eq "HASH";
    for my $molecule (@{$doc->{molecules}}) {
        $molecule->{props}{Forcefield} = $self->{type};
        $molecule->{props}{PartialChargeMethod} = $charges || "Momany-Rone";
        $_->{props}{ForcefieldType} = "T" . $_->{props}{Element} for @{$molecule->{atoms}};
    }
    return Ffdm::applyForceFieldCompleted();
}
sub Save { my ($self, $path) = @_; DiscoveryScript::_write($path, "FAKE-FORCEFIELD $self->{type}\n") }

# ---------------------------------------------------------------- Protocol (Pipeline Pilot protocols)
package Protocol;
sub taskComplete () { "taskComplete" }
sub taskFailed () { "taskFailed" }
sub taskError () { "taskError" }
sub taskRunning () { "taskRunning" }

sub LaunchProtocol {
    my ($name, $map, $server) = @_;
    my $doc = Protocol::Document::Create($name, Protocol::Document::DefaultSession());
    $doc->ReplaceItem($_, $map->{values}{$_}) for @{$map->{keys}};
    my $task = Protocol::Document::DefaultSession()->Launch($doc);
    $task->WaitForCompletion;
    return $task;
}

# The real parameter lists of the protocols MILO scripts use (defaults as a fresh protocol document shows them).
our %KNOWN = (
    "Minimization" => [
        ["Input Typed Molecule", "", "Mdm::Molecule"], ["Minimization Algorithm", "Smart Minimizer", "StringType"],
        ["Minimization Max Steps", 200, "LongType"], ["Minimization RMS Gradient", 0.1, "DoubleType"],
        ["Minimization Energy Change", 0.0, "DoubleType"], ["Minimization Save Results Frequency", 0, "LongType"],
        ["Implicit Solvent Model", "None", "StringType"], ["Dielectric Constant", 1, "DoubleType"],
        ["Nonbond List Radius", 14, "DoubleType"], ["Electrostatics", "Automatic", "StringType"],
    ],
    "Calculate Energy" => [
        ["Input Typed Molecule", "", "Mdm::Molecule"], ["Implicit Solvent Model", "None", "StringType"],
        ["Dielectric Constant", 1, "DoubleType"], ["Electrostatics", "Automatic", "StringType"],
    ],
    # The real list, types and defaults, as Discovery Studio 2026 dumped them on the VM (Savinase pH run,
    # 2026-09-25). The type names (BoolType, DoubleType, LongType, StringType) are the real ones.
    "Prepare Proteins" => [
        ["Advanced", "", "GroupType"],
        ["Build Loops", 1, "BoolType"],
        ["Disulfide Bridges", "", "StringType"],
        ["Energy Cutoff", 0.9, "DoubleType"],
        ["Flexible Stem Residues", 0, "LongType"],
        ["Forcefield", "CHARMm", "StringType"],
        ["Input Protein Molecules", "shortcut:/Discovery Studio Data/data/PDB/1ACC.pdb", "ProteinsType"],
        ["Ionic Strength", 0.145, "DoubleType"],
        ["Keep Ligands", 1, "BoolType"],
        ["Keep Water", "None", "StringType"],
        ["Loop Definition", "SEQRES", "StringType"],
        ["Loop List", "", "StringType"],
        ["Maximal Loop Length", 20, "LongType"],
        ["Parallel Processing", 0, "BoolType"],
        ["Parallel Processing Batch Size", 1, "LongType"],
        ["Parallel Processing Preserve Order", 1, "BoolType"],
        ["Parallel Processing Server", "localhost", "StringType"],
        ["Parallel Processing Server Processes", "2", "StringType"],
        ["Parallel Processing Server Run On Grid", 1, "BoolType"],
        ["Parallel Processing Server Run On Grid Queue Name", "", "StringType"],
        ["Protein Dielectric Constant", 10, "DoubleType"],
        ["Protonate", 1, "BoolType"],
        ["Reporting", "", "GroupType"],
        ["Reporting Stylesheet", "{42691EC8-0CE0-4DB9-8B3C-5379CBB967A7}", "StylesheetType"],
        ["Use CHARMm Minimization", 1, "BoolType"],
        ["Use Looper", 0, "BoolType"],
        ["Use Looper Maximal Loop Length", 12, "LongType"],
        ["pH for Protonation", 7.4, "DoubleType"],
    ],
);
# Every protocol that has run on the VM: its real parameter list, which MILO keeps from each run's dump
# (milo_app/mcp/protocols.py), so the checker refuses what the real protocol would refuse.
if (my $dir = $ENV{MILO_FAKE_DS_PROTOCOLS}) {
    if (opendir(my $dh, $dir)) {
        for my $file (sort grep { /\.tsv$/ } readdir $dh) {
            open(my $fh, "<", "$dir/$file") or next;
            my ($name, @rows);
            while (my $line = <$fh>) {
                $line =~ s/[\r\n]+$//;
                if ($line =~ /^# protocol: (.+)$/) { $name = $1; next }
                next if $line =~ /^#/ || $line =~ /^name\ttype\t/ || !length $line;
                my ($key, $type, $value) = split /\t/, $line, 3;
                push @rows, [$key, $value // "", $type // "StringType"];
            }
            $KNOWN{$name} = \@rows if defined $name && @rows;
        }
    }
}

package Protocol::ParameterMap;
sub Create { return bless {keys => [], values => {}}, "Protocol::ParameterMap" }
sub AddItem { my ($self, $k, $v) = @_; push @{$self->{keys}}, $k unless exists $self->{values}{$k}; $self->{values}{$k} = $v }
sub ReplaceItem { goto &AddItem }
sub ItemExists { exists $_[0]{values}{$_[1]} }
sub Item { $_[0]{values}{$_[1]} }
sub Count { scalar @{$_[0]{keys}} }
sub FirstKey { $_[0]{keys}[0] }
sub NextKey {
    my ($self, $key) = @_;
    for my $i (0 .. $#{$self->{keys}} - 1) { return $self->{keys}[$i + 1] if $self->{keys}[$i] eq $key }
    return undef;
}

package Protocol::Session;
sub Server { "localhost:9943" }
sub User { "milo-check" }
sub RunFolder { $_[0]{RunFolder} }
sub IsConnected { 1 }
my $RUNS = 0;
sub Launch {
    my ($self, $doc) = @_;
    $doc = $doc->{Document} if ref $doc eq "HASH";
    my $run = File::Spec->catdir($self->{RunFolder}, sprintf("%s_%d", $doc->{name} =~ s/\W+/_/gr, ++$RUNS));
    return Protocol::Task->new($doc, $run);
}

package Protocol::Document;
our $AUTOLOAD;
sub DefaultSession {
    return undef if $DiscoveryScript::SERVER_DOWN;
    return bless {RunFolder => File::Spec->catdir(Cwd::getcwd(), "Protocol Runs"), Server => "localhost:9943",
                  User => "milo-check"}, "Protocol::Session";
}
sub Create {
    my ($name, $session) = @_;
    ($name, $session) = @{$name}{qw(ProtocolName Session)} if ref $name;
    print "MILO-CHECK: protocol '$name' has never run on the VM: its parameter names and types were not checked\n"
        unless exists $Protocol::KNOWN{$name};
    my $map = Protocol::ParameterMap::Create();
    my %types;
    for my $p (@{$Protocol::KNOWN{$name} || []}) { $map->AddItem($p->[0], $p->[1]); $types{$p->[0]} = $p->[2] }
    return bless {name => $name, map => $map, types => \%types, known => exists $Protocol::KNOWN{$name}}, "Protocol::Document";
}
sub Name { $_[0]{name} }
sub Guid { "00000000-fake-guid-0000-" . length($_[0]{name}) }
sub Version { 1 }
sub ParameterMap { $_[0]{map} }
sub ItemExists { my ($self, $k) = @_; return $self->{known} ? $self->{map}->ItemExists($k) : 1 }
sub Item { $_[0]{map}->Item($_[1]) }
sub ParameterType { $_[0]{types}{$_[1]} || "StringType" }
sub ReplaceItem {
    my ($self, $k, $v) = @_;
    ($k, $v) = ($k->{Key}, exists $k->{Value} ? $k->{Value} : $k->{Parameter}) if ref $k;
    die "Protocol '$self->{name}' has no parameter '$k'\n" unless $self->ItemExists($k);
    # Refused as the real one refuses (seen on the VM, both with no message): a word such as "False" for a
    # BoolType, and a Perl integer such as 7 (no real-number value) for a DoubleType.
    my $type = $self->ParameterType($k);
    if (defined $v && !ref $v) {
        my $flags = B::svref_2object(\$v)->FLAGS;
        die "\n" if $type eq "BoolType" && !($flags & (B::SVf_IOK() | B::SVf_NOK()));
        die "\n" if $type eq "DoubleType" && ($flags & B::SVf_IOK()) && !($flags & B::SVf_NOK());
    }
    $self->{map}->ReplaceItem($k, $v);
}
sub Save {
    my ($self, $path) = @_;
    $path = $path->{Path} if ref $path;
    DiscoveryScript::_write($path, "<Protocol name='$self->{name}'>"
        . join("", map { "<Parameter name='$_'>" . ($self->{map}->Item($_) // "") . "</Parameter>" } @{$self->{map}{keys}})
        . "</Protocol>\n");
}
sub Close { 1 }
sub AUTOLOAD { my $self = shift; (my $m = $AUTOLOAD) =~ s/.*:://; return if $m eq "DESTROY"; DiscoveryScript::Stub->new("Protocol.$m") }

package Protocol::Task;
my $TASKS = 0;
sub new {
    my ($class, $doc, $run) = @_;
    return bless {doc => $doc, RunPath => $run, TaskId => ++$TASKS, ProtocolName => $doc->{name},
                  Host => "localhost", State => "taskRunning"}, $class;
}
sub ProtocolName { $_[0]{ProtocolName} }
sub TaskId { $_[0]{TaskId} }
sub Host { $_[0]{Host} }
sub RunPath { $_[0]{RunPath} }
sub State { $_[0]{State} }
sub IsRunnable { $_[0]{State} eq "taskRunning" }
sub IsProtocolSuccessful { $_[0]{State} eq "taskComplete" ? 1 : 0 }
sub ProtocolStatus { $_[0]{State} eq "taskComplete" ? "Success" : "Failed" }
sub LogMessages { DiscoveryScript::FakeArray->new(@{$_[0]{log} || []}) }
sub KillTask { $_[0]{State} = "taskFailed" }
sub WaitForCompletion {
    my ($self) = @_;
    return $self->{State} unless $self->{State} eq "taskRunning";
    my $doc = $self->{doc};
    my $out = File::Spec->catdir($self->{RunPath}, "Output");
    my @log = ("Protocol $doc->{name} started (fake)");
    # Molecule-valued parameters: run "on" them and write a result structure with energy properties.
    for my $key (@{$doc->{map}{keys}}) {
        my $value = $doc->{map}->Item($key);
        next unless defined $value && !ref $value && $value =~ /\.(dsv|msv|mol2|sd|mol|pdb)$/i && -f $value;
        my $mdm = Mdm::Document::_load($value);
        for my $molecule (@{$mdm->{molecules}}) {
            $molecule->{props}{"Potential Energy"} = -12.345;
            $molecule->{props}{"Van der Waals Energy"} = 1.5;
            $molecule->{props}{"Electrostatic Energy"} = -20.25;
            $molecule->{props}{"Initial Potential Energy"} = 3.75;
            $molecule->{props}{"RMS Gradient"} = 0.0931;
            # docking protocols tag every pose they return with its score (placeholder values, not physics)
            if ($doc->{name} =~ /CDOCKER/i) {
                $molecule->{props}{"-CDOCKER_ENERGY"} = 25.5;
                $molecule->{props}{"-CDOCKER_INTERACTION_ENERGY"} = 30.25;
            }
        }
        $mdm->Save(File::Spec->catfile($out, "Output.dsv"), "dsv");
        push @log, "Processed $key";
    }
    DiscoveryScript::_write(File::Spec->catfile($out, "Output.dsv"), "FAKE-MDM dsv\n") unless -f File::Spec->catfile($out, "Output.dsv");
    DiscoveryScript::_write(File::Spec->catfile($self->{RunPath}, "Report.htm"), "<html>$doc->{name} (fake report)</html>\n");
    DiscoveryScript::_write(File::Spec->catfile($self->{RunPath}, "Output", "$doc->{name}.log"), join("\n", @log) . "\n");
    push @log, "Protocol $doc->{name} completed (fake)";
    $self->{log} = \@log;
    $self->{State} = "taskComplete";
    return $self->{State};
}

package DiscoveryScript;
1;
