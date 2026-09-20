import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk
import yt_dlp


class SuperSlowPlayer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Super Slow Motion Player")
        self.geometry("900x750")
        self.minsize(800, 600)

        # プレイヤー状態管理
        self.cap = None
        self.total_frames = 0
        self.fps = 30.0
        self.current_frame_idx = 0
        self.is_playing = False
        self.playback_speed = 0.10  # 初期速度 0.1x
        self.temp_file_path = None
        self.is_scrubbing = False

        self._build_ui()
        self._bind_keys()

    def _build_ui(self):
        # 1. URL・ファイル入力エリア
        top_frame = ttk.LabelFrame(self, text="動画の読み込み", padding=10)
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(top_frame, text="YouTube URL:").pack(side=tk.LEFT)
        self.url_entry = ttk.Entry(top_frame, width=45)
        self.url_entry.pack(side=tk.LEFT, padx=5)

        self.btn_load_yt = ttk.Button(top_frame, text="取得・解析", command=self.load_youtube)
        self.btn_load_yt.pack(side=tk.LEFT, padx=5)

        self.btn_load_local = ttk.Button(top_frame, text="ローカル動画を開く", command=self.load_local_file)
        self.btn_load_local.pack(side=tk.LEFT, padx=5)

        self.lbl_status = ttk.Label(top_frame, text="待機中", foreground="gray")
        self.lbl_status.pack(side=tk.LEFT, padx=10)

        # 2. 動画描画キャンバス
        self.canvas_frame = tk.Frame(self, bg="black")
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(self.canvas_frame, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 3. シークバー
        seek_frame = tk.Frame(self, padx=10)
        seek_frame.pack(fill=tk.X, pady=2)

        self.timeline_var = tk.DoubleVar()
        self.timeline = ttk.Scale(
            seek_frame,
            from_=0,
            to=100,
            variable=self.timeline_var,
            orient=tk.HORIZONTAL,
            command=self._on_timeline_drag
        )
        self.timeline.pack(fill=tk.X, expand=True, side=tk.LEFT)
        self.timeline.bind("<ButtonRelease-1>", self._on_timeline_release)

        self.lbl_time = ttk.Label(seek_frame, text="00:00 / 00:00 (F: 0)")
        self.lbl_time.pack(side=tk.RIGHT, padx=5)

        # 4. 操作パネル（再生速度・コマ送り・キャプチャ）
        ctrl_frame = ttk.LabelFrame(self, text="再生コントロール", padding=10)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=5)

        # 再生・コマ送りボタン
        btn_box = tk.Frame(ctrl_frame)
        btn_box.pack(side=tk.LEFT, padx=5)

        self.btn_prev = ttk.Button(btn_box, text="◀ 1コマ戻る (,)", width=14, command=lambda: self.step_frame(-1))
        self.btn_prev.pack(side=tk.LEFT, padx=2)

        self.btn_play = ttk.Button(btn_box, text="再生 / 停止 (Space)", width=16, command=self.toggle_play)
        self.btn_play.pack(side=tk.LEFT, padx=2)

        self.btn_next = ttk.Button(btn_box, text="1コマ進む (.) ▶", width=14, command=lambda: self.step_frame(1))
        self.btn_next.pack(side=tk.LEFT, padx=2)

        # 再生速度調整
        speed_box = tk.Frame(ctrl_frame)
        speed_box.pack(side=tk.LEFT, padx=20)

        ttk.Label(speed_box, text="速度:").pack(side=tk.LEFT)
        self.speed_label = ttk.Label(speed_box, text=f"{self.playback_speed:.2f}x", font=("Monospace", 10, "bold"), width=7)
        self.speed_label.pack(side=tk.LEFT)

        self.speed_slider = ttk.Scale(
            speed_box,
            from_=0.01,
            to=1.00,
            value=self.playback_speed,
            orient=tk.HORIZONTAL,
            command=self._on_speed_change,
            length=140
        )
        self.speed_slider.pack(side=tk.LEFT, padx=5)

        # プリセット速度ボタン
        for rate in [0.05, 0.10, 0.25, 1.00]:
            btn = ttk.Button(speed_box, text=f"{rate}x", width=5, command=lambda r=rate: self.set_speed(r))
            btn.pack(side=tk.LEFT, padx=1)

        # 静止画保存ボタン
        self.btn_snap = ttk.Button(ctrl_frame, text="現在フレームを保存", command=self.capture_frame)
        self.btn_snap.pack(side=tk.RIGHT, padx=5)

    def _bind_keys(self):
        # キーボードショートカット
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<comma>", lambda e: self.step_frame(-1))
        self.bind("<period>", lambda e: self.step_frame(1))
        self.bind("<Left>", lambda e: self.step_frame(-5))
        self.bind("<Right>", lambda e: self.step_frame(5))
        self.bind("z", lambda e: self.set_speed(0.05))
        self.bind("x", lambda e: self.set_speed(0.10))
        self.bind("c", lambda e: self.set_speed(0.25))
        self.bind("v", lambda e: self.set_speed(1.00))

    def load_youtube(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("警告", "YouTubeのURLを入力してください。")
            return

        self.lbl_status.config(text="動画を解析・キャッシュ中...", foreground="blue")
        self.btn_load_yt.config(state=tk.DISABLED)

        # 非同期でダウンロード＆ロード
        threading.Thread(target=self._download_and_open_youtube, args=(url,), daemon=True).start()

    def _download_and_open_youtube(self, url):
        try:
            # 安定した超スロー・コマ送りのため、720p以下の軽量MP4として一時保存
            temp_dir = tempfile.gettempdir()
            out_template = os.path.join(temp_dir, f"yt_slow_{int(time.time())}.mp4")

            ydl_opts = {
                'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
                'outtmpl': out_template,
                'quiet': True,
                'no_warnings': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            self.temp_file_path = out_template
            self.after(0, lambda: self._open_video(self.temp_file_path))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("エラー", f"YouTubeの取得に失敗しました:\n{e}"))
            self.after(0, lambda: self.lbl_status.config(text="取得失敗", foreground="red"))
        finally:
            self.after(0, lambda: self.btn_load_yt.config(state=tk.NORMAL))

    def load_local_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("動画ファイル", "*.mp4 *.mov *.avi *.mkv *.webm"), ("すべてのファイル", "*.*")]
        )
        if path:
            self._open_video(path)

    def _open_video(self, file_path):
        if self.cap:
            self.cap.release()

        self.cap = cv2.VideoCapture(file_path)
        if not self.cap.isOpened():
            messagebox.showerror("エラー", "動画を開けませんでした。")
            return

        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.current_frame_idx = 0
        self.timeline.config(to=self.total_frames - 1)
        self.lbl_status.config(text="読み込み完了", foreground="green")

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

        # 時間表示の更新
        current_sec = frame_idx / self.fps
        total_sec = self.total_frames / self.fps
        self.lbl_time.config(
            text=f"{int(current_sec // 60):02d}:{int(current_sec % 60):02d} / "
                 f"{int(total_sec // 60):02d}:{int(total_sec % 60):02d} (F: {frame_idx})"
        )

        # 画面サイズに合わせてアスペクト比を維持してリサイズ
        c_w = self.canvas.winfo_width()
        c_h = self.canvas.winfo_height()
        if c_w < 10 or c_h < 10:
            c_w, c_h = 800, 450

        img_h, img_w, _ = frame.shape
        scale = min(c_w / img_w, c_h / img_h)
        new_w, new_h = int(img_w * scale), int(img_h * scale)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        self.photo = ImageTk.PhotoImage(image=Image.fromarray(resized))
        self.canvas.delete("all")
        self.canvas.create_image(c_w // 2, c_h // 2, anchor=tk.CENTER, image=self.photo)

    def _play_loop(self):
        if not self.is_playing or not self.cap:
            return

        if self.current_frame_idx < self.total_frames - 1:
            self.show_frame(self.current_frame_idx + 1)
            # 再生速度に応じたディレイ（ミリ秒）
            # 通常30fps = 約33ms / 0.1倍速 = 約330ms
            delay = int(1000 / (self.fps * self.playback_speed))
            self.after(max(1, delay), self._play_loop)
        else:
            self.is_playing = False
            self.btn_play.config(text="再生 (Space)")

    def toggle_play(self):
        if not self.cap:
            return
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.btn_play.config(text="一時停止 (Space)")
            self._play_loop()
        else:
            self.btn_play.config(text="再生 (Space)")

    def step_frame(self, step):
        if not self.cap:
            return
        self.is_playing = False
        self.btn_play.config(text="再生 (Space)")
        self.show_frame(self.current_frame_idx + step)

    def set_speed(self, rate):
        self.playback_speed = float(rate)
        self.speed_slider.set(rate)
        self.speed_label.config(text=f"{rate:.2f}x")

    def _on_speed_change(self, val):
        rate = float(val)
        self.playback_speed = rate
        self.speed_label.config(text=f"{rate:.2f}x")

    def _on_timeline_drag(self, val):
        self.is_scrubbing = True
        self.show_frame(int(float(val)))

    def _on_timeline_release(self, event):
        self.is_scrubbing = False

    def capture_frame(self):
        if not self.cap:
            return
        save_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 画像", "*.png"), ("JPEG 画像", "*.jpg")]
        )
        if save_path:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_idx)
            ret, frame = self.cap.read()
            if ret:
                cv2.imwrite(save_path, frame)
                messagebox.showinfo("保存完了", f"現在のフレームを保存しました:\n{save_path}")

    def destroy(self):
        if self.cap:
            self.cap.release()
        # 一時ファイルの削除
        if self.temp_file_path and os.path.exists(self.temp_file_path):
            try:
                os.remove(self.temp_file_path)
            except OSError:
                pass
        super().destroy()


if __name__ == "__main__":
    app = SuperSlowPlayer()
    app.mainloop()