using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("数据分析工作台")]
[assembly: AssemblyDescription("Data Analysis 原生桌面启动器")]
[assembly: AssemblyProduct("Data Analysis")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

internal static class Launcher
{
    private static readonly Encoding Utf8 = new UTF8Encoding(false);

    [STAThread]
    private static int Main(string[] args)
    {
        bool check = args.Length == 1 && args[0] == "--check";
        if (args.Length > 0)
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), Utf8) { AutoFlush = true });
            Console.SetError(new StreamWriter(Console.OpenStandardError(), Utf8) { AutoFlush = true });
        }
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string log = Path.Combine(root, "out", "gui", "startup_check.log");
        try
        {
            if (args.Length > 0 && !check)
                throw new ArgumentException("启动器仅接受 --check 参数；日常使用请直接双击打开。");
            string python = Path.Combine(root, "python", "python.exe");
            string pythonw = Path.Combine(root, "python", "pythonw.exe");
            string app = Path.Combine(root, "gui", "app.py");
            Require(python, "缺少便携 Python，请将完整的 python 文件夹与启动器放在一起。");
            Require(pythonw, "缺少 python\\pythonw.exe，请检查便携 Python 是否完整。");
            Require(app, "缺少 gui\\app.py，请检查项目是否完整。");
            Directory.CreateDirectory(Path.GetDirectoryName(log));
            int code;
            string details = Check(python, app, root, out code);
            File.WriteAllText(log, details, Utf8);
            if (code != 0)
                throw new InvalidOperationException("启动环境检查未通过。\n\n" + Tail(details, 1600));
            if (check)
            {
                Console.Out.Write(details);
                return 0;
            }
            using (Process child = Process.Start(StartInfo(pythonw, "-I -B -X utf8 " + Quote(app), root)))
            {
                if (child == null)
                    throw new InvalidOperationException("未能创建工作台进程，请重试。");
                // 短时间内退出时报告失败；正常窗口和已有窗口激活交给GUI管理。
                if (child.WaitForExit(1200) && child.ExitCode != 0)
                    throw new InvalidOperationException("工作台启动失败，请查看 out\\gui\\startup_error.log。");
            }
            return 0;
        }
        catch (Exception error)
        {
            string message = error.Message + "\n\n项目目录：" + root;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(log));
                File.WriteAllText(Path.Combine(Path.GetDirectoryName(log), "launcher_error.log"),
                                  DateTime.Now.ToString("s") + "\n" + error.ToString(), Utf8);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            if (check || args.Length > 0)
                Console.Error.WriteLine(message);
            else
            {
                Application.EnableVisualStyles();
                MessageBox.Show(message + "\n\n诊断记录：out\\gui\\launcher_error.log", "无法启动数据分析工作台",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            return 1;
        }
    }

    private static void Require(string path, string message)
    {
        if (!File.Exists(path))
            throw new FileNotFoundException(message, path);
    }

    private static ProcessStartInfo StartInfo(string file, string arguments, string root)
    {
        var info = new ProcessStartInfo(file, arguments);
        info.WorkingDirectory = root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.EnvironmentVariables.Remove("PYTHONHOME");
        info.EnvironmentVariables.Remove("PYTHONPATH");
        info.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        info.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        return info;
    }

    private static string Check(string python, string app, string root, out int code)
    {
        var output = new StringBuilder();
        var info = StartInfo(python, "-I -B -X utf8 " + Quote(app) + " --check", root);
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.StandardOutputEncoding = Utf8;
        info.StandardErrorEncoding = Utf8;
        using (var process = new Process())
        {
            process.StartInfo = info;
            DataReceivedEventHandler collect = delegate(object sender, DataReceivedEventArgs data)
            {
                if (data.Data == null) return;
                lock (output)
                {
                    if (output.Length < 65536) output.AppendLine(data.Data);
                }
            };
            process.OutputDataReceived += collect;
            process.ErrorDataReceived += collect;
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            if (!process.WaitForExit(30000))
            {
                process.Kill();
                process.WaitForExit();
                throw new TimeoutException("启动检查超过30秒，请检查便携 Python，稍后重试。");
            }
            process.WaitForExit();
            code = process.ExitCode;
        }
        return output.ToString();
    }

    private static string Quote(string text)
    {
        // 参数只有由项目目录确定的文件路径，不接收任意命令或拼接Shell。
        return "\"" + text + "\"";
    }

    private static string Tail(string text, int length)
    {
        return text.Length <= length ? text : text.Substring(text.Length - length);
    }
}
