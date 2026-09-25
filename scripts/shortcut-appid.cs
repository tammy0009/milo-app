// Give a .lnk the same Windows AppUserModelID as the running MILO process.
// WScript.Shell can create the shortcut, but does not expose its property store.
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
internal class ShellLink { }

[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPersistFile
{
    void GetClassID(out Guid classId);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string fileName);
}

[StructLayout(LayoutKind.Sequential)]
internal struct PropertyKey
{
    public Guid FormatId;
    public uint PropertyId;
}

[StructLayout(LayoutKind.Explicit, Size = 24)]
internal struct PropVariant
{
    [FieldOffset(0)] public ushort Type;
    [FieldOffset(8)] public IntPtr Value;
}

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IPropertyStore
{
    void GetCount(out uint count);
    void GetAt(uint index, out PropertyKey key);
    void GetValue(ref PropertyKey key, out PropVariant value);
    void SetValue(ref PropertyKey key, ref PropVariant value);
    void Commit();
}

public static class ShortcutAppId
{
    private static readonly PropertyKey AppIdKey = new PropertyKey {
        FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
        PropertyId = 5
    };

    public static void Set(string shortcutPath, string appId)
    {
        var link = new ShellLink();
        try
        {
            ((IPersistFile)link).Load(shortcutPath, 2); // STGM_READWRITE
            var value = new PropVariant {
                Type = 31, // VT_LPWSTR
                Value = Marshal.StringToCoTaskMemUni(appId)
            };
            try
            {
                var key = AppIdKey;
                ((IPropertyStore)link).SetValue(ref key, ref value);
                ((IPropertyStore)link).Commit();
                ((IPersistFile)link).Save(shortcutPath, true);
            }
            finally { Marshal.FreeCoTaskMem(value.Value); }
        }
        finally { Marshal.ReleaseComObject(link); }
    }

    public static string Get(string shortcutPath)
    {
        var link = new ShellLink();
        try
        {
            ((IPersistFile)link).Load(shortcutPath, 0);
            var key = AppIdKey;
            PropVariant value;
            ((IPropertyStore)link).GetValue(ref key, out value);
            try { return value.Type == 31 ? Marshal.PtrToStringUni(value.Value) : null; }
            finally { PropVariantClear(ref value); }
        }
        finally { Marshal.ReleaseComObject(link); }
    }

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref PropVariant value);
}
