using System.Diagnostics;
using System.Windows.Forms;

static string FindProjectRoot()
{
    var dir = AppContext.BaseDirectory;
    while (!string.IsNullOrWhiteSpace(dir))
    {
        if (File.Exists(Path.Combine(dir, "desktop", "package.json")) &&
            File.Exists(Path.Combine(dir, "server.py")))
        {
            return dir;
        }

        var parent = Directory.GetParent(dir);
        if (parent is null)
        {
            break;
        }
        dir = parent.FullName;
    }

    return AppContext.BaseDirectory;
}

static void ShowError(string message)
{
    MessageBox.Show(message, "Agent Workbench 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
}

try
{
    var projectRoot = FindProjectRoot();
    var desktopDir = Path.Combine(projectRoot, "desktop");
    var electronExe = Path.Combine(desktopDir, "node_modules", "electron", "dist", "electron.exe");
    var packageJson = Path.Combine(desktopDir, "package.json");

    if (!File.Exists(packageJson))
    {
        ShowError($"未找到桌面端目录：{desktopDir}");
        return;
    }

    if (!File.Exists(electronExe))
    {
        ShowError(
            "未找到 Electron 运行时。\n\n" +
            $"请先在以下目录执行一次 npm install：\n{desktopDir}"
        );
        return;
    }

    var startInfo = new ProcessStartInfo
    {
        FileName = electronExe,
        Arguments = ".",
        WorkingDirectory = desktopDir,
        UseShellExecute = false,
        CreateNoWindow = true,
    };

    Process.Start(startInfo);
}
catch (Exception ex)
{
    ShowError(ex.Message);
}
