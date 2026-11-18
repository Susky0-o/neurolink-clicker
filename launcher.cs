using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

class Launcher
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [STAThread]
    static void Main(string[] args)
    {
        try { SetCurrentProcessExplicitAppUserModelID("NeuraLink.Clicker"); }
        catch { }

        string dir = Path.GetDirectoryName(Application.ExecutablePath);
        string bat = Path.Combine(dir, "启动连点器.bat");

        if (!File.Exists(bat))
        {
            MessageBox.Show(
                "同目录下找不到「启动连点器.bat」，请把 exe 和 bat 放在一起。",
                "NEUROLINK 连点器",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning);
            return;
        }

        try
        {
            ProcessStartInfo psi = new ProcessStartInfo(bat);
            psi.WorkingDirectory = dir;
            psi.UseShellExecute = true;
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "启动失败：" + ex.Message,
                "NEUROLINK 连点器",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
