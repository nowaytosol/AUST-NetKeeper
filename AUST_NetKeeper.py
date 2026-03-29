import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import requests
import time
import datetime
import threading
import json
import os
import sys
import logging
import winreg
from tkinter import Menu
from cryptography.fernet import Fernet
import queue
from PIL import Image, ImageDraw
import pystray

CONFIG_FILE = "aust_net_config.json"
KEY_FILE = ".aust_net.key"
LOGIN_URL = "http://10.255.0.19/drcom/login"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('aust_login.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class ConfigManager:
    def __init__(self, config_file):
        self.config_file = config_file
        self.fernet = self._get_fernet()
        self.config = self.load()
    
    def _get_fernet(self):
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(KEY_FILE, "wb") as f:
                f.write(key)
            logging.info("生成新的加密密钥")
        return Fernet(key)
    
    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "password" in data and data["password"]:
                        data["password"] = self._decrypt(data["password"])
                    return data
            except Exception as e:
                logging.error(f"加载配置失败: {e}")
        return {
            "username": "",
            "password": "",
            "isp": "学生电信出口 (@aust)",
            "auto_run": False,
            "reconnect_interval": 5,
            "check_interval": 30
        }
    
    def save(self, config):
        config_copy = config.copy()
        if "password" in config_copy and config_copy["password"]:
            config_copy["password"] = self._encrypt(config_copy["password"])
        
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config_copy, f, ensure_ascii=False, indent=2)
            self.config = config
            return True
        except Exception as e:
            logging.error(f"保存配置失败: {e}")
            return False
    
    def _encrypt(self, text):
        return self.fernet.encrypt(text.encode()).decode()
    
    def _decrypt(self, text):
        try:
            return self.fernet.decrypt(text.encode()).decode()
        except Exception:
            return text

def create_tray_icon_image():
    # 生成一个简单的蓝色图标，打包成 exe 后可在任务栏托盘显示
    image = Image.new('RGB', (64, 64), color='#2196F3')
    draw = ImageDraw.Draw(image)
    draw.ellipse([16, 16, 48, 48], fill='white')
    draw.rectangle([30, 24, 34, 40], fill='#2196F3')
    draw.rectangle([24, 30, 40, 34], fill='#2196F3')
    return image

class AutoLoginApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AUST 校园网守护者")
        self.root.geometry("450x520")
        self.root.resizable(False, False)
        
        self.is_running = False
        self.thread = None
        self.stop_event = threading.Event()
        self.tray_icon = None
        self.log_queue = queue.Queue()
        
        self.config_manager = ConfigManager(CONFIG_FILE)
        
        # 严格按照网关源码扒出的真实出口暗号
        self.isp_map = {
            "学生电信出口 (@aust)": "@aust",
            "学生移动出口 (@cmcc)": "@cmcc",
            "学生联通出口 (@unicom)": "@unicom",
            "教职工出口 (@jzg)": "@jzg"
        }

        self.create_widgets()
        self.load_config()
        self.update_log_from_queue()
    
    def create_widgets(self):
        title_frame = tk.Frame(self.root)
        title_frame.pack(pady=(15, 10))
        tk.Label(title_frame, text="AUST 校园网守护者", font=("微软雅黑", 14, "bold")).pack()
        
        input_frame = tk.Frame(self.root)
        input_frame.pack(pady=10)
        
        tk.Label(input_frame, text="学号 (纯数字):").grid(row=0, column=0, sticky=tk.W, pady=8)
        self.entry_user = tk.Entry(input_frame, width=28)
        self.entry_user.grid(row=0, column=1, pady=8)
        
        tk.Label(input_frame, text="密码:").grid(row=1, column=0, sticky=tk.W, pady=8)
        self.entry_pwd = tk.Entry(input_frame, width=28, show="*")
        self.entry_pwd.grid(row=1, column=1, pady=8)
        
        tk.Label(input_frame, text="网络出口:").grid(row=2, column=0, sticky=tk.W, pady=8)
        self.isp_var = tk.StringVar()
        self.isp_combo = ttk.Combobox(input_frame, textvariable=self.isp_var, state="readonly", width=26)
        self.isp_combo['values'] = list(self.isp_map.keys())
        self.isp_combo.current(0)
        self.isp_combo.grid(row=2, column=1, pady=8)
        
        settings_frame = tk.LabelFrame(self.root, text="高级设置", padx=10, pady=5)
        settings_frame.pack(pady=5, padx=20, fill="x")
        
        tk.Label(settings_frame, text="重连间隔(秒):").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.reconnect_interval_var = tk.IntVar(value=5)
        tk.Entry(settings_frame, textvariable=self.reconnect_interval_var, width=8).grid(row=0, column=1, pady=5, padx=5)
        
        tk.Label(settings_frame, text="检测间隔(秒):").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(20,0))
        self.check_interval_var = tk.IntVar(value=30)
        tk.Entry(settings_frame, textvariable=self.check_interval_var, width=8).grid(row=0, column=3, pady=5, padx=5)
        
        self.auto_run_var = tk.BooleanVar()
        tk.Checkbutton(input_frame, text="开机静默启动 (最小化到托盘)", variable=self.auto_run_var).grid(row=4, columnspan=2, pady=10)
        
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=15)
        
        self.btn_start = tk.Button(btn_frame, text="▶ 开始运行", bg="lightgreen", width=12, font=("微软雅黑", 10), command=self.start_monitor)
        self.btn_start.pack(side=tk.LEFT, padx=10)
        
        self.btn_stop = tk.Button(btn_frame, text="⏹ 停止运行", bg="salmon", width=12, font=("微软雅黑", 10), state=tk.DISABLED, command=self.stop_monitor)
        self.btn_stop.pack(side=tk.LEFT, padx=10)
        
        self.btn_clear = tk.Button(btn_frame, text="🗑 清空日志", bg="lightblue", width=10, font=("微软雅黑", 9), command=self.clear_log)
        self.btn_clear.pack(side=tk.LEFT, padx=10)
        
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(self.root, textvariable=self.status_var, font=("微软雅黑", 10), fg="#666")
        self.status_label.pack(pady=5)
        
        tk.Label(self.root, text="运行日志:", font=("微软雅黑", 9)).pack()
        self.log_area = scrolledtext.ScrolledText(self.root, width=48, height=10, state=tk.DISABLED, bg="#f5f5f5", font=("Consolas", 9))
        self.log_area.pack(pady=5, padx=10)
        
        self.create_menu()
    
    def create_menu(self):
        menubar = Menu(self.root)
        self.root.config(menu=menubar)
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="菜单", menu=file_menu)
        file_menu.add_command(label="显示窗口", command=self.show_window)
        file_menu.add_command(label="最小化到托盘", command=self.minimize_to_tray)
        file_menu.add_separator()
        file_menu.add_command(label="完全退出", command=self.quit_app)
    
    def log(self, message):
        self.log_queue.put(message)
    
    def update_log_from_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_area.config(state=tk.NORMAL)
                timestamp = datetime.datetime.now().strftime('%H:%M:%S')
                self.log_area.insert(tk.END, f"[{timestamp}] {message}\n")
                self.log_area.see(tk.END)
                self.log_area.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.update_log_from_queue)
    
    def clear_log(self):
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state=tk.DISABLED)
    
    def validate_input(self):
        user = self.entry_user.get().strip()
        password = self.entry_pwd.get().strip()
        
        if not user or not user.isdigit():
            messagebox.showwarning("提示", "学号必须填写且只能为纯数字！")
            return None
        if not password:
            messagebox.showwarning("提示", "请输入密码！")
            return None
        
        try:
            reconnect = self.reconnect_interval_var.get()
            check = self.check_interval_var.get()
        except Exception:
            messagebox.showwarning("提示", "高级设置中的时间间隔必须是有效的数字！")
            return None
            
        if reconnect < 1 or reconnect > 60:
            messagebox.showwarning("提示", "重连间隔应在1-60秒之间！")
            return None
        if check < 5 or check > 300:
            messagebox.showwarning("提示", "检测间隔应在5-300秒之间！")
            return None
        
        return {
            "username": user,
            "password": password,
            "isp": self.isp_var.get(),
            "auto_run": self.auto_run_var.get(),
            "reconnect_interval": reconnect,
            "check_interval": check
        }
    
    def load_config(self):
        config = self.config_manager.config
        self.entry_user.insert(0, config.get("username", ""))
        self.entry_pwd.insert(0, config.get("password", ""))
        saved_isp = config.get("isp", "学生电信出口 (@aust)")
        if saved_isp in self.isp_map:
            self.isp_combo.set(saved_isp)
        self.auto_run_var.set(config.get("auto_run", False))
        self.reconnect_interval_var.set(config.get("reconnect_interval", 5))
        self.check_interval_var.set(config.get("check_interval", 30))
        
        # 如果设置了开机自启，启动时尝试自动开始监控（并可配合启动参数直接最小化）
        if self.auto_run_var.get() and config.get("username") and config.get("password"):
            # 延迟 2 秒启动，确保系统网络模块已加载完毕
            self.root.after(2000, self.start_monitor)
    
    def apply_auto_run_to_system(self, enable):
        """完全合规的系统注册表读写，实现静默开机自启"""
        try:
            if getattr(sys, 'frozen', False):
                app_path = sys.executable
            else:
                app_path = os.path.abspath(__file__)
                
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
            if enable:
                winreg.SetValueEx(key, "AUST_NetKeeper", 0, winreg.REG_SZ, f'"{app_path}" -autorun')
            else:
                try:
                    winreg.DeleteValue(key, "AUST_NetKeeper")
                except FileNotFoundError:
                    pass 
            winreg.CloseKey(key)
        except Exception as e:
            logging.error(f"设置开机自启失败: {e}")
            self.log("警告: 开机自启设置可能被安全软件拦截，请手动放行。")

    def save_config(self, validated_data):
        config = {
            "username": validated_data["username"],
            "password": validated_data["password"],
            "isp": validated_data["isp"],
            "auto_run": validated_data["auto_run"],
            "reconnect_interval": validated_data["reconnect_interval"],
            "check_interval": validated_data["check_interval"]
        }
        self.apply_auto_run_to_system(validated_data["auto_run"])
        return self.config_manager.save(config)
    
    def check_internet(self):
        """终极优化版：使用 Dr.COM 官方 API 进行毫秒级状态检测"""
        status_url = "http://10.255.0.19/drcom/chkstatus"
        params = {"callback": "dr1002"}
        try:
            # 局域网请求极其迅速，超时设为1秒，彻底释放系统资源
            response = requests.get(status_url, params=params, timeout=1)
            response.encoding = 'utf-8'
            
            # 分析网关底层返回特征码
            if '"result":1' in response.text or '"time":' in response.text:
                return True
            else:
                return False
        except requests.RequestException:
            # 捕获局域网断开异常（如拔掉网线）
            return False
    
    def login_gateway(self, full_username, password):
        params = {
            "callback": "dr1003",
            "DDDDD": full_username,
            "upass": password,
            "0MKKey": "123456",
            "R1": "0", "R3": "0", "R6": "0", "para": "00", "v6ip": "", "v": "3404"
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"
        }
        try:
            response = requests.get(LOGIN_URL, params=params, headers=headers, timeout=5)
            if response.status_code == 200:
                self.log("向网关发射认证请求成功！")
                self.status_var.set("认证中...")
                return True
            else:
                self.log(f"网关拒绝请求: {response.status_code}")
                return False
        except Exception as e:
            self.log(f"网络通信故障: {e}")
            return False
    
    def monitor_loop(self, full_username, password, reconnect_interval, check_interval):
        self.log(f"守护引擎已启动 | 账号: {full_username.split('@')[0]}***")
        self.log(f"官方 API 探测频率: {check_interval} 秒/次")
        
        while not self.stop_event.is_set():
            try:
                if not self.check_internet():
                    self.log("警报: 网关连接掉线，正在极速重连...")
                    self.status_var.set("正在重连...")
                    self.login_gateway(full_username, password)
                    
                    # 重连后的冷却时间
                    for _ in range(reconnect_interval):
                        if self.stop_event.is_set():
                            break
                        time.sleep(1)
                else:
                    self.status_var.set("🟢 已连接")
                
                # 正常状态下的探测休眠周期
                for _ in range(check_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logging.error(f"引擎异常: {e}")
                time.sleep(5)
        
        self.log("守护引擎已安全关闭")
        self.status_var.set("已停止")
    
    def start_monitor(self):
        # 防止重复启动
        if self.is_running:
            return
            
        validated = self.validate_input()
        if not validated:
            return

        suffix = self.isp_map.get(validated["isp"], "")
        full_username = validated["username"] + suffix

        if not self.save_config(validated):
            messagebox.showwarning("提示", "配置文件保存失败！")
        
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.entry_user.config(state=tk.DISABLED)
        self.entry_pwd.config(state=tk.DISABLED)
        self.isp_combo.config(state=tk.DISABLED)
        self.status_var.set("启动中...")
        
        self.is_running = True
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self.monitor_loop,
            args=(full_username, validated["password"], validated["reconnect_interval"], validated["check_interval"]),
            daemon=True
        )
        self.thread.start()
    
    def stop_monitor(self):
        self.is_running = False
        self.stop_event.set()
        self.log("正在刹车，安全退出监控队列...")
        
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.entry_user.config(state=tk.NORMAL)
        self.entry_pwd.config(state=tk.NORMAL)
        self.isp_combo.config(state=tk.NORMAL)
    
    def minimize_to_tray(self):
        self.root.withdraw()
        if self.tray_icon is None:
            try:
                image = create_tray_icon_image()
                menu = pystray.Menu(
                    pystray.MenuItem("打开主控制面板", self.show_window),
                    pystray.MenuItem("退出守护程序", self.quit_app)
                )
                self.tray_icon = pystray.Icon("AUST_Login", image, "AUST校园网守护者", menu)
                self.tray_icon.run_detached()
            except Exception as e:
                logging.error(f"系统托盘创建失败: {e}")
                self.root.deiconify()
    
    def show_window(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.after(0, self.root.deiconify)
        self.root.focus_force() # 强行调出置顶
    
    def quit_app(self):
        if self.is_running:
            self.stop_monitor()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.quit()

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoLoginApp(root)
    
    # 拦截右上角 X 按钮，改为静默后台
    root.protocol("WM_DELETE_WINDOW", app.minimize_to_tray)
    
    # 解析命令行参数：如果注册表带了 -autorun 启动，则直接隐藏窗口进入托盘
    if len(sys.argv) > 1 and sys.argv[1] == "-autorun":
        root.withdraw()
        # 延迟1秒创建托盘，防止系统启动初期UI渲染失败
        root.after(1000, app.minimize_to_tray)
        
    root.mainloop()