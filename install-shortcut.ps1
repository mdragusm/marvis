# Creates/refreshes the Marvis shortcuts (Start Menu + project root) and stamps each with
# System.AppUserModel.ID = "Marvis.Assistant" -- the SAME id indicator.py sets at runtime
# via SetCurrentProcessExplicitAppUserModelID.
#
# Why this is needed: when you pin the RUNNING app to the taskbar, Windows anchors the pin
# to a Start Menu shortcut whose AppUserModel.ID matches the window's. With no matching
# shortcut, it falls back to the launching exe (pythonw.exe main.py) and shows a generic
# "Python document" icon instead of icon.ico. The WScript.Shell COM object can't set that
# property, so we go through IShellLink/IPropertyStore directly.
#
# Path-portable: everything derives from $PSScriptRoot, so it works after copying the folder
# to another PC. Safe to re-run. After running, pin from the Start Menu (search "Marvis") or
# unpin any stale taskbar pin and re-pin the running app.

$ErrorActionPreference = 'Stop'

$root      = $PSScriptRoot
$aumid     = 'Marvis.Assistant'
$pythonw   = Join-Path $root '.venv\Scripts\pythonw.exe'
$main      = Join-Path $root 'main.py'
$icon      = Join-Path $root 'marvis\indicator_assets\icon.ico'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$smLnk     = Join-Path $startMenu 'Marvis.lnk'
$rootLnk   = Join-Path $root 'Marvis.lnk'

$cs = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace MarvisShortcut {

  [StructLayout(LayoutKind.Sequential)]
  public struct PropertyKey {
    public Guid fmtid;
    public uint pid;
    public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; }
  }

  // 24 bytes on x64 (8-byte header + 16-byte union), 16 on x86 -- matches PROPVARIANT.
  [StructLayout(LayoutKind.Sequential)]
  public struct PropVariant {
    public ushort vt;
    public ushort r1;
    public ushort r2;
    public ushort r3;
    public IntPtr p;
    public IntPtr p2;
  }

  [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
  public class CShellLink { }

  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
   Guid("000214F9-0000-0000-C000-000000000046")]
  public interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
  }

  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
   Guid("0000010b-0000-0000-C000-000000000046")]
  public interface IPersistFile {
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
  }

  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
   Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
  public interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PropertyKey pkey);
    void GetValue(ref PropertyKey key, out PropVariant pv);
    void SetValue(ref PropertyKey key, ref PropVariant pv);
    void Commit();
  }

  public static class Helper {
    static readonly Guid AppUserModelIDGuid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
    const ushort VT_LPWSTR = 31;

    public static void SetShortcut(string lnkPath, string target, string args, string workDir, string icon, string aumid) {
      IShellLinkW link = (IShellLinkW)(new CShellLink());
      IPersistFile pf = (IPersistFile)link;

      // Always build fresh -- loading an existing .lnk read-only and then saving to the
      // same path trips STG_E_ACCESSDENIED. We set every field we care about anyway.
      if (System.IO.File.Exists(lnkPath)) { System.IO.File.Delete(lnkPath); }
      link.SetPath(target);
      link.SetArguments(args ?? "");
      link.SetWorkingDirectory(workDir ?? "");
      if (!string.IsNullOrEmpty(icon)) link.SetIconLocation(icon, 0);
      link.SetDescription("Launch Marvis");

      IPropertyStore store = (IPropertyStore)link;
      PropertyKey key = new PropertyKey(AppUserModelIDGuid, 5);
      PropVariant pv = new PropVariant();
      pv.vt = VT_LPWSTR;
      pv.p = Marshal.StringToCoTaskMemUni(aumid);
      store.SetValue(ref key, ref pv);
      store.Commit();
      Marshal.FreeCoTaskMem(pv.p);

      pf.Save(lnkPath, true);
      Marshal.ReleaseComObject(store);
      Marshal.ReleaseComObject(pf);
      Marshal.ReleaseComObject(link);
    }
  }
}
'@

Add-Type -TypeDefinition $cs -Language CSharp

if (-not (Test-Path $startMenu)) { New-Item -ItemType Directory -Path $startMenu -Force | Out-Null }

$argStr = '"' + $main + '"'
[MarvisShortcut.Helper]::SetShortcut($smLnk,   $pythonw, $argStr, $root, $icon, $aumid)
[MarvisShortcut.Helper]::SetShortcut($rootLnk, $pythonw, $argStr, $root, $icon, $aumid)

Write-Host "Marvis shortcuts installed (Start Menu + project root), tagged AppUserModel.ID=$aumid." -ForegroundColor Green
Write-Host "Pin from the Start Menu (search 'Marvis'), or unpin any old taskbar pin and re-pin the running app."
