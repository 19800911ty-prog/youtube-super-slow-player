import os
import sys
import tempfile
import threading
import time
import subprocess
import urllib.request
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk


class SuperSlowMaster(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Super Slow Motion Analysis Studio")
        self.geometry("1000x820")
        self.minsize(850, 650)
        self.configure(bg="#1a1a1a")

        # 再生・解析パラメータ
        self.cap = None
        self.total_frames = 0
        self.fps = 30.0
        self.current_frame_idx = 0
        self.is_playing = False
        self.playback_speed = 0.10
        self.temp_file_path = None
        self.is_scrubbing = False

        # 区間リピート（A-Bループ）
        self.loop_a = None
        self.loop_b = None

        # ズーム & パン
        self.zoom_level = 1.0
        self.pan_x = 0.5
        self.pan_y = 0.5
        self.drag_start_x = 0
        self.drag_start_y = 0

        self._init_style()
        self._build_ui()
        self._bind_events()

    def _init_style(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure(".", background="#1a1a1a", foreground="#ffffff", font=("Segoe UI", 9))
        self.style.configure("TLabel", background="#1a1a1a", foreground="#e0e0e0")
        self.style.configure("TButton", background="#333333", foreground="#ffffff", borderwidth=1)
        self.style.map("TButton", background=[("active", "#4d4d4d")])
        self.style.configure("TEntry", fieldbackground="#2b2b2b", foreground="#ffffff")
        self.style.configure("TLabelframe", background="#1a1a1a", foreground="#3ea6ff")
        self.style.configure("TLabelframe.Label", background="#1a1a1a", foreground="#3ea6ff", font=("Segoe UI", 9, "bold"))

    def _build_ui(self):
        # 1. ヘッダー / 入力部
        header = ttk.LabelFrame(self, text=" ソース選択 ", padding=8)
        header.pack(fill=tk.X, padx=12, pady=(8, 4))

        ttk.Label(header, text="YouTube URL:").pack(side=tk.LEFT, padx=4)
        self.url_entry = ttk.Entry(header, width=45)
        self.url_entry.pack(side=tk.LEFT, padx=4)

        self.btn_load_yt = ttk.Button(header, text="取得・解析", command=self.load_youtube)
        self.btn_load_yt.pack(side=tk.LEFT, padx=4)

        self.btn_load_local = ttk.Button(header, text="PC内の動画を開く", command=self.load_local_file)
        self.btn_load_local.pack(side=tk.LEFT, padx=4)

        self.lbl_status = ttk.Label(header, text="待機中", foreground="#888888")
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        # 2. ビデオ描画キャンバス
        self.canvas_container = tk.Frame(self, bg="#000000")
        self.canvas_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self.canvas = tk.Canvas(self.canvas_container, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.lbl_zoom_info = tk.Label(
            self.canvas_container,
            text="Zoom: 1.0x (ホイールで拡大 / ドラッグで移動)",
            bg="#111111",
            fg="#888888",
            font=("Segoe UI", 8)
        )
        self.lbl_zoom_info.place(x=8, y=8)

        # 3. タイムライン
        timeline_box = tk.Frame(self, bg="#1a1a1a")
        timeline_box.pack(fill=tk.X, padx=12, pady=2)

        self.timeline_var = tk.DoubleVar()
        self.timeline = ttk.Scale(
            timeline_box,
            from_=0,
            to=100,
            variable=self.timeline_var,
            orient=tk.HORIZONTAL,
            command=self._on_timeline_drag
        )
        self.timeline.pack(fill=tk.X, side=tk.LEFT, expand=True)
        self.timeline.bind("<ButtonRelease-1>", self._on_timeline_release)

        self.lbl_time = ttk.Label(timeline_box, text="00:00.00 / 00:00.00 | Frame: 0", font=("Consolas", 9))
        self.lbl_time.pack(side=tk.RIGHT, padx=(8, 0))

        # 4. コントロールパネル
        ctrl_panel = ttk.LabelFrame(self, text=" 再生・解析コントロール ", padding=8)
        ctrl_panel.pack(fill=tk.X, padx=12, pady=(4, 10))

        step_group = tk.Frame(ctrl_panel, bg="#1a1a1a")
        step_group.pack(side=tk.LEFT, padx=5)

        ttk.Button(step_group, text="◀◀ -5F", width=6, command=lambda: self.step_frame(-5)).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_group, text="◀ 1F (,)", width=7, command=lambda: self.step_frame(-1)).pack(side=tk.LEFT, padx=1)
        self.btn_play = ttk.Button(step_group, text="再生 (Space)", width=12, command=self.toggle_play)
        self.btn_play.pack(side=tk.LEFT, padx=3)
        ttk.Button(step_group, text="1F ( .) ▶", width=7, command=lambda: self.step_frame(1)).pack(side=tk.LEFT, padx=1)
        ttk.Button(step_group, text="+5F ▶▶", width=6, command=lambda: self.step_frame(5)).pack(side=tk.LEFT, padx=1)

        speed_group = tk.Frame(ctrl_panel, bg="#1a1a1a")
        speed_group.pack(side=tk.LEFT, padx=15)

        ttk.Label(speed_group, text="速度:").pack(side=tk.LEFT)
        self.lbl_speed = ttk.Label(speed_group, text=f"{self.playback_speed:.2f}x", font=("Consolas", 10, "bold"), foreground="#3ea6ff", width=6)
        self.lbl_speed.pack(side=tk.LEFT)

        self.speed_slider = ttk.Scale(
            speed_group,
            from_=0.01,
            to=1.00,
            value=self.playback_speed,
            orient=tk.HORIZONTAL,
            command=self._on_speed_slider,
            length=110
        )
        self.speed_slider.pack(side=tk.LEFT, padx=4)

        for rate in [0.02, 0.05, 0.10, 0.25, 1.00]:
            ttk.Button(speed_group, text=f"{rate}x", width=5, command=lambda r=rate: self.set_speed(r)).pack(side=tk.LEFT, padx=1)

        loop_group = tk.Frame(ctrl_panel, bg="#1a1a1a")
        loop_group.pack(side=tk.LEFT, padx=15)

        ttk.Button(loop_group, text="[ A点設定", width=8, command=self.set_loop_a).pack(side=tk.LEFT, padx=1)
        ttk.Button(loop_group, text="B点設定 ]", width=8, command=self.set_loop_b).pack(side=tk.LEFT, padx=1)
        ttk.Button(loop_group, text="解除", width=5, command=self.clear_loop).pack(side=tk.LEFT, padx=1)
        self.lbl_loop = ttk.Label(loop_group, text="A-B: OFF", font=("Consolas", 8), foreground="#888888")
        self.lbl_loop.pack(side=tk.LEFT, padx=4)

        tool_group = tk.Frame(ctrl_panel, bg="#1a1a1a")
        tool_group.pack(side=tk.RIGHT, padx=5)

        ttk.Button(tool_group, text="ズーム解除", width=9, command=self.reset_zoom).pack(side=tk.LEFT, padx=2)
        ttk.Button(tool_group, text="高画質保存", width=11, command=self.capture_frame).pack(side=tk.LEFT, padx=2)

    def _bind_events(self):
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<comma>", lambda e: self.step_frame(-1))
        self.bind("<period>", lambda e: self.step_frame(1))
        self.bind("<Left>", lambda e: self.step_frame(-5))
        self.bind("<Right>", lambda e: self.step_frame(5))
        self.bind("z", lambda e: self.set_speed(0.05))
        self.bind("x", lambda e: self.set_speed(0.10))
        self.bind("c", lambda e: self.set_speed(0.25))
        self.bind("v", lambda e: self.set_speed(1.00))
        self.bind("[", lambda e: self.set_loop_a())
        self.bind("]", lambda e: self.set_loop_b())
        self.bind("\\", lambda e: self.clear_loop())

        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom(1.15, e.x, e.y))
        self.canvas.bind("<Button-5>", lambda e: self._zoom(0.85, e.x, e.y))
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)

    def load_youtube(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("警告", "YouTube URLを入力してください。")
            return

        self.lbl_status.config(text="準備中...", foreground="#3ea6ff")
        self.btn_load_yt.config(state=tk.DISABLED)

        threading.Thread(target=self._fetch_yt_thread, args=(url,), daemon=True).start()

    def _fetch_yt_thread(self, url):
        try:
            temp_dir = tempfile.gettempdir()
            out_file = os.path.join(temp_dir, f"yt_cache_{int(time.time())}.mp4")
            
            # 外部エンジン(yt-dlp.exe)の配置確認と自動ダウンロード
            base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
            local_bin = os.path.join(base_dir, "yt-dlp.exe")
            temp_bin = os.path.join(temp_dir, "yt-dlp.exe")
            ytdlp_bin = local_bin if os.path.exists(local_bin) else temp_bin

            if not os.path.exists(ytdlp_bin):
                self.after(0, lambda: self.lbl_status.config(text="エンジン自動取得中...", foreground="#3ea6ff"))
                engine_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
                urllib.request.urlretrieve(engine_url, ytdlp_bin)

            self.after(0, lambda: self.lbl_status.config(text="動画を解析・キャッシュ中...", foreground="#3ea6ff"))

            # コマンドプロンプト画面を出さずにバックグラウンド実行
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            cmd = [
                ytdlp_bin,
                "--extractor-args", "youtube:player_client=android,web",
                "-f", "best[ext=mp4][height<=720]/best[height<=720]/best",
                "-o", out_file,
                "--no-playlist",
                "--no-check-certificates",
                url
            ]

            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr or "動画のダウンロードに失敗しました。")

            self.temp_file_path = out_file
            self.after(0, lambda: self._open_video_source(self.temp_file_path))
        except Exception as err:
            err_msg = str(err)
            self.after(0, lambda: messagebox.showerror("読込エラー", f"YouTube取得に失敗しました:\n{err_msg[:250]}"))
            self.after(0, lambda: self.lbl_status.config(text="取得失敗", foreground="#ff4444"))
        finally:
            self.after(0, lambda: self.btn_load_yt.config(state=tk.NORMAL))

    def load_local_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("動画ファイル", "*.mp4 *.mov *.avi *.mkv *.webm"), ("全ファイル", "*.*")]
        )
        if path:
            self._open_video_source(path)

    def _open_video_source(self, path):
        if self.cap:
            self.cap.release()

        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            messagebox.showerror("エラー", "動画ストリームを開けませんでした。")
            return

        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.current_frame_idx = 0
        self.timeline.config(to=max(1, self.total_frames - 1))
        self.lbl_status.config(text="読込完了", foreground="#00e676")

        self.clear_loop()
        self.reset_zoom()
        self.show_frame(0)
        self.is_playing = False
        self.btn_play.config(text="再生 (Space)")

    def show_frame(self, frame_idx):
        if not self.cap:
            return

        frame_idx = max(0, min(frame_idx, self.total_frames - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.cap.read()
        if not ret:
            return

        self.current_frame_idx = frame_idx
        if not self.is_scrubbing:
            self.timeline_var.set(frame_idx)

        curr_sec = frame_idx / self.fps
        total_sec = self.total_frames / self.fps
        self.lbl_time.config(
            text=f"{int(curr_sec//60):02d}:{curr_sec%60:05.2f} / "
                 f"{int(total_sec//60):02d}:{total_sec%60:05.2f} | F: {frame_idx}"
        )

        orig_h, orig_w, _ = frame.shape
        crop_w = int(orig_w / self.zoom_level)
        crop_h = int(orig_h / self.zoom_level)

        cx = int(orig_w * self.pan_x)
        cy = int(orig_h * self.pan_y)

        x1 = max(0, min(orig_w - crop_w, cx - crop_w // 2))
        y1 = max(0, min(orig_h - crop_h, cy - crop_h // 2))

        cropped_frame = frame[y1:y1 + crop_h, x1:x1 + crop_w]

        canv_w = self.canvas.winfo_width()
        canv_h = self.canvas.winfo_height()
        if canv_w < 10 or canv_h < 10:
            canv_w, canv_h = 960, 540

        scale = min(canv_w / crop_w, canv_h / crop_h)
        target_w, target_h = max(1, int(crop_w * scale)), max(1, int(crop_h * scale))

        rgb = cv2.cvtColor(cropped_frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        self.photo = ImageTk.PhotoImage(image=Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(canv_w // 2, canv_h // 2, anchor=tk.CENTER, image=self.photo)

    def _on_mouse_wheel(self, event):
        factor = 1.15 if event.delta > 0 else 0.85
        self._zoom(factor, event.x, event.y)

    def _zoom(self, factor, mouse_x, mouse_y):
        if not self.cap:
            return
        new_zoom = max(1.0, min(8.0, self.zoom_level * factor))
        if new_zoom == self.zoom_level:
            return

        self.zoom_level = new_zoom
        if self.zoom_level == 1.0:
            self.pan_x, self.pan_y = 0.5, 0.5
        self.lbl_zoom_info.config(text=f"Zoom: {self.zoom_level:.1f}x (ホイールで拡大 / ドラッグで移動)")
        self.show_frame(self.current_frame_idx)

    def _on_drag_start(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def _on_drag_motion(self, event):
        if self.zoom_level <= 1.0 or not self.cap:
            return

        dx = (event.x - self.drag_start_x) / (self.canvas.winfo_width() * self.zoom_level)
        dy = (event.y - self.drag_start_y) / (self.canvas.winfo_height() * self.zoom_level)

        self.pan_x = max(0.0, min(1.0, self.pan_x - dx))
        self.pan_y = max(0.0, min(1.0, self.pan_y - dy))

        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.show_frame(self.current_frame_idx)

    def reset_zoom(self):
        self.zoom_level = 1.0
        self.pan_x, self.pan_y = 0.5, 0.5
        self.lbl_zoom_info.config(text="Zoom: 1.0x (ホイールで拡大 / ドラッグで移動)")
        self.show_frame(self.current_frame_idx)

    def _play_loop(self):
        if not self.is_playing or not self.cap:
            return

        next_frame = self.current_frame_idx + 1

        if self.loop_b is not None and next_frame > self.loop_b:
            next_frame = self.loop_a if self.loop_a is not None else 0
        elif next_frame >= self.total_frames:
            if self.loop_a is not None:
                next_frame = self.loop_a
            else:
                self.is_playing = False
                self.btn_play.config(text="再生 (Space)")
                return

        self.show_frame(next_frame)
        delay = int(1000 / (self.fps * self.playback_speed))
        self.after(max(1, delay), self._play_loop)

    def toggle_play(self):
        if not self.cap:
            return
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.btn_play.config(text="一時停止 (Space)")
            self._play_loop()
        else:
            self.btn_play.config(text="再生 (Space)")

    def step_frame(self, delta):
        if not self.cap:
            return
        self.is_playing = False
        self.btn_play.config(text="再生 (Space)")
        self.show_frame(self.current_frame_idx + delta)

    def set_speed(self, rate):
        self.playback_speed = float(rate)
        self.speed_slider.set(rate)
        self.lbl_speed.config(text=f"{rate:.2f}x")

    def _on_speed_slider(self, val):
        rate = float(val)
        self.playback_speed = rate
        self.lbl_speed.config(text=f"{rate:.2f}x")

    def _on_timeline_drag(self, val):
        self.is_scrubbing = True
        self.show_frame(int(float(val)))

    def _on_timeline_release(self, event):
        self.is_scrubbing = False

    def set_loop_a(self):
        self.loop_a = self.current_frame_idx
        self._update_loop_ui()

    def set_loop_b(self):
        self.loop_b = self.current_frame_idx
        self._update_loop_ui()

    def clear_loop(self):
        self.loop_a = None
        self.loop_b = None
        self._update_loop_ui()

    def _update_loop_ui(self):
        a_str = str(self.loop_a) if self.loop_a is not None else "--"
        b_str = str(self.loop_b) if self.loop_b is not None else "--"
        color = "#00e676" if (self.loop_a is not None or self.loop_b is not None) else "#888888"
        self.lbl_loop.config(text=f"A-B: [{a_str} - {b_str}]", foreground=color)

    def capture_frame(self):
        if not self.cap:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG画像 (ロスレス)", "*.png"), ("JPEG画像", "*.jpg")]
        )
        if path:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_idx)
            ret, frame = self.cap.read()
            if ret:
                cv2.imwrite(path, frame)
                messagebox.showinfo("保存成功", f"原寸フレーム画像を書き出しました:\n{path}")

    def destroy(self):
        if self.cap:
            self.cap.release()
        if self.temp_file_path and os.path.exists(self.temp_file_path):
            try:
                os.remove(self.temp_file_path)
            except OSError:
                pass
        super().destroy()


if __name__ == "__main__":
    app = SuperSlowMaster()
    app.mainloop()