using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace RoboMapLocalLauncher
{
    public class MainForm : Form
    {
        private readonly string root;
        private readonly string appRoot;
        private readonly string pythonExe;
        private readonly string scriptPath;
        private readonly string dataDir;
        private readonly string logDir;
        private readonly string logPath;
        private readonly string webViewDataDir;

        private Process backend;
        private int port = 8787;
        private bool healthCheckRunning;
        private bool webViewReady;
        private readonly object logLock = new object();

        private WebView2 webView;
        private Panel loadingPanel;
        private Label loadingTitle;
        private Label loadingDetail;
        private Label lanHint;
        private Button retryButton;
        private System.Windows.Forms.Timer healthTimer;
        private System.Windows.Forms.Timer activationTimer;
        private readonly System.Threading.EventWaitHandle activation = new System.Threading.EventWaitHandle(false, System.Threading.EventResetMode.AutoReset, "Local\\RoboMapLocal-Activate");

        public MainForm()
        {
            root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RoboMapLocal");
            appRoot = Path.Combine(root, "app");
            pythonExe = Path.Combine(root, "venv", "Scripts", "python.exe");
            scriptPath = Path.Combine(appRoot, "start_robomap.py");
            dataDir = Path.Combine(root, "data");
            logDir = Path.Combine(root, "logs");
            logPath = Path.Combine(logDir, "backend.log");
            webViewDataDir = Path.Combine(root, "webview2-v100");

            Directory.CreateDirectory(dataDir);
            Directory.CreateDirectory(logDir);
            Directory.CreateDirectory(webViewDataDir);

            BuildUi();
            activationTimer = new System.Windows.Forms.Timer();
            activationTimer.Interval = 200;
            activationTimer.Tick += delegate {
                if (!activation.WaitOne(0)) return;
                Show();
                WindowState = FormWindowState.Maximized;
                BringToFront();
                Activate();
                AppendLog("LAUNCHER: existing window restored");
            };
            activationTimer.Start();
            Shown += delegate { StartServer(); };
            FormClosing += OnFormClosing;
        }

        private void BuildUi()
        {
            Text = "RoboMap Local v10.7";
            Width = 1440;
            Height = 900;
            MinimumSize = new Size(940, 620);
            StartPosition = FormStartPosition.CenterScreen;
            WindowState = FormWindowState.Maximized;
            BackColor = Color.FromArgb(10, 14, 23);
            ForeColor = Color.White;
            Font = new Font("Segoe UI", 10F);
            KeyPreview = true;

            webView = new WebView2();
            webView.Dock = DockStyle.Fill;
            webView.Visible = false;
            Controls.Add(webView);

            loadingPanel = new Panel();
            loadingPanel.Dock = DockStyle.Fill;
            loadingPanel.BackColor = Color.FromArgb(10, 14, 23);
            Controls.Add(loadingPanel);
            loadingPanel.BringToFront();

            var center = new TableLayoutPanel();
            center.Dock = DockStyle.Fill;
            center.ColumnCount = 1;
            center.RowCount = 6;
            center.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            center.RowStyles.Add(new RowStyle(SizeType.Percent, 34F));
            center.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            center.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            center.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            center.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            center.RowStyles.Add(new RowStyle(SizeType.Percent, 66F));
            loadingPanel.Controls.Add(center);

            var logo = new Label();
            logo.Text = "R";
            logo.TextAlign = ContentAlignment.MiddleCenter;
            logo.Font = new Font("Segoe UI Semibold", 28F, FontStyle.Bold);
            logo.ForeColor = Color.FromArgb(0, 229, 255);
            logo.AutoSize = false;
            logo.Width = 72;
            logo.Height = 72;
            logo.Margin = new Padding(0, 0, 0, 14);
            logo.Anchor = AnchorStyles.None;
            center.Controls.Add(new Label(), 0, 0);
            center.Controls.Add(logo, 0, 1);

            loadingTitle = new Label();
            loadingTitle.Text = "RoboMap Local";
            loadingTitle.Font = new Font("Segoe UI Semibold", 24F, FontStyle.Bold);
            loadingTitle.TextAlign = ContentAlignment.MiddleCenter;
            loadingTitle.AutoSize = true;
            loadingTitle.Anchor = AnchorStyles.None;
            center.Controls.Add(loadingTitle, 0, 2);

            loadingDetail = new Label();
            loadingDetail.Text = "Uruchamiam lokalny backend i badawczą łatkę AutoPair LAN…";
            loadingDetail.ForeColor = Color.FromArgb(170, 180, 195);
            loadingDetail.TextAlign = ContentAlignment.MiddleCenter;
            loadingDetail.AutoSize = true;
            loadingDetail.Margin = new Padding(0, 10, 0, 8);
            loadingDetail.Anchor = AnchorStyles.None;
            center.Controls.Add(loadingDetail, 0, 3);

            var actions = new FlowLayoutPanel();
            actions.FlowDirection = FlowDirection.TopDown;
            actions.WrapContents = false;
            actions.AutoSize = true;
            actions.Anchor = AnchorStyles.None;
            center.Controls.Add(actions, 0, 4);

            lanHint = new Label();
            lanHint.Text = "Telefon / laptop: ustalam adres LAN…";
            lanHint.ForeColor = Color.FromArgb(120, 200, 210);
            lanHint.AutoSize = true;
            lanHint.TextAlign = ContentAlignment.MiddleCenter;
            lanHint.Margin = new Padding(0, 4, 0, 10);
            actions.Controls.Add(lanHint);

            retryButton = new Button();
            retryButton.Text = "RESTART BACKENDU";
            retryButton.AutoSize = true;
            retryButton.Height = 38;
            retryButton.Visible = false;
            retryButton.FlatStyle = FlatStyle.Flat;
            retryButton.FlatAppearance.BorderColor = Color.FromArgb(55, 70, 90);
            retryButton.BackColor = Color.FromArgb(20, 26, 41);
            retryButton.ForeColor = Color.White;
            retryButton.Click += delegate { RestartServer(); };
            actions.Controls.Add(retryButton);

            healthTimer = new System.Windows.Forms.Timer();
            healthTimer.Interval = 500;
            healthTimer.Tick += delegate { CheckHealthAsync(); };
            healthTimer.Start();

            KeyDown += delegate(object sender, KeyEventArgs e)
            {
                if (e.Control && e.KeyCode == Keys.R && webViewReady && webView.CoreWebView2 != null)
                {
                    webView.CoreWebView2.Reload();
                    e.SuppressKeyPress = true;
                }
                else if (e.KeyCode == Keys.F12 && webViewReady && webView.CoreWebView2 != null)
                {
                    try { webView.CoreWebView2.OpenDevToolsWindow(); } catch { }
                }
            };
        }

        private int FindFreePort()
        {
            for (int p = 8787; p <= 8797; p++)
            {
                TcpListener listener = null;
                try
                {
                    listener = new TcpListener(IPAddress.Any, p);
                    listener.Start();
                    listener.Stop();
                    return p;
                }
                catch
                {
                    if (listener != null) try { listener.Stop(); } catch { }
                }
            }
            throw new Exception("Porty 8787-8797 są zajęte.");
        }

        private string GetLanIp()
        {
            try
            {
                foreach (NetworkInterface ni in NetworkInterface.GetAllNetworkInterfaces())
                {
                    if (ni.OperationalStatus != OperationalStatus.Up || ni.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                    foreach (UnicastIPAddressInformation ua in ni.GetIPProperties().UnicastAddresses)
                    {
                        if (ua.Address.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(ua.Address))
                            return ua.Address.ToString();
                    }
                }
            }
            catch { }
            return "127.0.0.1";
        }

        private void StartServer()
        {
            try
            {
                if (backend != null && !backend.HasExited) return;
                if (!File.Exists(pythonExe)) throw new Exception("Brak prywatnego środowiska RoboMap. Uruchom ponownie instalator.");
                if (!File.Exists(scriptPath)) throw new Exception("Brak plików RoboMap. Uruchom ponownie instalator.");

                healthTimer.Start();
                port = FindFreePort();
                retryButton.Visible = false;
                loadingPanel.Visible = true;
                loadingPanel.BringToFront();
                loadingDetail.Text = "Uruchamiam lokalny backend i badawczą łatkę AutoPair LAN…";
                lanHint.Text = "Telefon / laptop: http://" + GetLanIp() + ":" + port;

                File.AppendAllText(logPath, "\r\n=== START " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " ===\r\n");

                var psi = new ProcessStartInfo();
                psi.FileName = pythonExe;
                psi.Arguments = "-u \"" + scriptPath + "\"";
                psi.WorkingDirectory = appRoot;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.EnvironmentVariables["DATA_DIR"] = dataDir;
                psi.EnvironmentVariables["ROBOMAP_PORT"] = port.ToString();
                psi.EnvironmentVariables["ROBOMAP_NO_BROWSER"] = "1";

                backend = new Process();
                backend.StartInfo = psi;
                backend.EnableRaisingEvents = true;
                backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) AppendLog(e.Data); };
                backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) AppendLog(e.Data); };
                backend.Exited += delegate(object sender, EventArgs e)
                {
                    try
                    {
                        BeginInvoke((MethodInvoker)delegate
                        {
                            if (!IsDisposed)
                            {
                                if (!Object.ReferenceEquals(sender, backend)) return;
                                if (backend.ExitCode == 0) { Environment.Exit(0); return; }
                                loadingPanel.Visible = true;
                                loadingPanel.BringToFront();
                                loadingDetail.Text = "Backend zatrzymał się. Szczegóły zapisano w logu.";
                                retryButton.Visible = true;
                            }
                        });
                    }
                    catch { }
                };

                if (!backend.Start()) throw new Exception("Nie udało się uruchomić backendu RoboMap.");
                backend.BeginOutputReadLine();
                backend.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                AppendLog("LAUNCHER ERROR: " + ex);
                loadingDetail.Text = ex.Message;
                retryButton.Visible = true;
            }
        }

        private void RestartServer()
        {
            StopServer();
            webViewReady = false;
            webView.Visible = false;
            System.Threading.Thread.Sleep(200);
            StartServer();
        }

        private void StopServer()
        {
            try
            {
                if (backend != null && !backend.HasExited)
                {
                    backend.Kill();
                    backend.WaitForExit(2500);
                }
            }
            catch { }
            backend = null;
        }

        private void CheckHealthAsync()
        {
            if (healthCheckRunning) return;
            healthCheckRunning = true;
            System.Threading.ThreadPool.QueueUserWorkItem(delegate
            {
                bool ok = false;
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/health");
                    req.Timeout = 450;
                    req.Proxy = null;
                    req.ReadWriteTimeout = 450;
                    using (var resp = (HttpWebResponse)req.GetResponse())
                    using (var reader = new StreamReader(resp.GetResponseStream()))
                    {
                        var body = reader.ReadToEnd();
                        var health = new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<System.Collections.Generic.Dictionary<string, object>>(body);
                        ok = resp.StatusCode == HttpStatusCode.OK && health.ContainsKey("service") && (string)health["service"] == "robomap-local" && health.ContainsKey("ok") && Object.Equals(health["ok"], true);
                    }
                }
                catch { ok = false; }
                finally { healthCheckRunning = false; }

                if (!ok) return;
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (!webViewReady) InitializeWebView();
                    });
                }
                catch { }
            });
        }

        private async void InitializeWebView()
        {
            if (webViewReady) return;
            webViewReady = true;
            try
            {
                var runtimeVersion = CoreWebView2Environment.GetAvailableBrowserVersionString();
                loadingDetail.Text = "Backend online. WebView2 " + runtimeVersion + ". Ładuję pełny panel RoboMap…";
                if (webView.CoreWebView2 == null) {
                    var env = await CoreWebView2Environment.CreateAsync(null, webViewDataDir);
                    await webView.EnsureCoreWebView2Async(env);
                }
                webView.CoreWebView2.Settings.AreDevToolsEnabled = true;
                webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
                webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                webView.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
                {
                    e.Handled = true;
                    try { Process.Start(e.Uri); } catch { }
                };
                webView.Source = new Uri("http://127.0.0.1:" + port + "/?v=10.7&t=" + DateTimeOffset.Now.ToUnixTimeSeconds());
                webView.CoreWebView2.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e) { AppendLog("WEBVIEW navigation: " + (e.IsSuccess ? "OK" : e.WebErrorStatus.ToString())); };
                webView.Visible = true;
                webView.BringToFront();
                loadingPanel.Visible = false;
            }
            catch (Exception ex)
            {
                webViewReady = false;
                healthTimer.Stop();
                AppendLog("WEBVIEW2 ERROR: " + ex);
                loadingPanel.Visible = true;
                loadingPanel.BringToFront();
                loadingDetail.Text = "WebView2 nie wystartował: " + ex.GetType().Name + ": " + ex.Message + "\r\nOtwieram ten sam panel awaryjnie w Edge.";
                retryButton.Visible = true;
                try
                {
                    var edge = new ProcessStartInfo();
                    edge.FileName = "msedge.exe";
                    edge.Arguments = "--app=\"http://127.0.0.1:" + port + "/\"";
                    edge.UseShellExecute = true;
                    Process.Start(edge);
                }
                catch
                {
                    try { Process.Start("http://127.0.0.1:" + port + "/"); } catch { }
                }
            }
        }

        private void AppendLog(string line)
        {
            try
            {
                lock (logLock) File.AppendAllText(logPath, line + Environment.NewLine);
            }
            catch { }
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            e.Cancel = true;
            Hide();
        }
    }

    internal static class Program
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr window, int command);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr window);
        [System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode)]
        private static extern IntPtr FindWindow(string className, string title);
        [STAThread]
        private static void Main()
        {
            bool createdNew;
            using (var mutex = new System.Threading.Mutex(true, "Local\\RoboMapLocal-v100-SingleInstance", out createdNew))
            {
                if (!createdNew)
                {
                    using (var activation = new System.Threading.EventWaitHandle(false, System.Threading.EventResetMode.AutoReset, "Local\\RoboMapLocal-Activate")) activation.Set();
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm());
            }
        }
    }
}
