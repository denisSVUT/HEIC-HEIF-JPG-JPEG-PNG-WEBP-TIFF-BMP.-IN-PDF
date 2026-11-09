"""GUI for HEIC -> PDF, three case volumes, entirely offline."""
from __future__ import annotations

import json
import os
import queue
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from pdf_engine import Photo, create_pdfs, load_photo, scan_folder

VERSION = '1.0'
SORT_CHOICES = {
    'Имя файла — снизу вверх': 'name_reverse',
    'Имя файла — сверху вниз': 'name_forward',
    'Дата изменения — от новых к старым': 'date_reverse',
    'Дата изменения — от старых к новым': 'date_forward',
}
MAX_SIDE_CHOICES = {
    'Исходный размер — максимум деталей': 0,
    'До 3500 пикселей — хороший баланс': 3500,
    'До 2500 пикселей — компактнее': 2500,
}


class ReaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f'HEIC → PDF | Три тома дела · v{VERSION}')
        root.geometry('1270x800')
        root.minsize(1060, 650)
        self.photos: list[Photo] = []
        self.source_dir: Path | None = None
        self.tom2: Photo | None = None
        self.tom3: Photo | None = None
        self.active_preview = None
        self.active_index: int | None = None
        self.task_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.exporting = False
        self.progress_value = tk.DoubleVar(value=0)
        self.status_text = tk.StringVar(value='Выберите папку с фотографиями HEIC, JPG, PNG...')
        self.summary_text = tk.StringVar(value='Пока не загружены фотографии')
        self.file_title = tk.StringVar(value='Выберите страницу слева')
        self.file_detail = tk.StringVar(value='Встроенный просмотр HEIC. Изображения не отправляются в интернет.')
        self.sort_var = tk.StringVar(value=next(iter(SORT_CHOICES)))
        self.size_var = tk.StringVar(value='Исходный размер — максимум деталей')
        self.quality_var = tk.IntVar(value=90)
        self.combine_var = tk.BooleanVar(value=True)
        self._setup_theme()
        self._build_ui()
        self.root.after(200, self._poll)

    def _setup_theme(self):
        st = ttk.Style(self.root)
        if 'vista' in st.theme_names():
            st.theme_use('vista')
        elif 'clam' in st.theme_names():
            st.theme_use('clam')
        st.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
        st.configure('Subtitle.TLabel', font=('Segoe UI', 10))
        st.configure('Section.TLabel', font=('Segoe UI', 11, 'bold'))
        st.configure('Doc.Treeview', rowheight=27, font=('Segoe UI', 10))
        st.configure('Doc.Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        st.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'), padding=(13, 8))
        st.configure('Toolbar.TButton', padding=(8, 6))

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=(16, 10))
        main.pack(fill='both', expand=True)
        header = ttk.Frame(main)
        header.pack(fill='x')
        ttk.Label(header, text='HEIC → PDF', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='   Материалы дела  ·  три тома  ·  локальная обработка', style='Subtitle.TLabel').pack(side='left', pady=(8, 0))
        ttk.Button(header, text='Открыть проект', command=self.open_project, style='Toolbar.TButton').pack(side='right', padx=(8, 0))
        ttk.Button(header, text='Сохранить проект', command=self.save_project, style='Toolbar.TButton').pack(side='right')
        top = ttk.Frame(main)
        top.pack(fill='x', pady=(15, 8))
        ttk.Button(top, text='1. Выбрать папку с фотографиями', command=self.choose_folder, style='Accent.TButton').pack(side='left')
        ttk.Label(top, text='     Порядок: ').pack(side='left')
        ttk.Combobox(top, textvariable=self.sort_var, values=list(SORT_CHOICES), state='readonly', width=34).pack(side='left')
        ttk.Button(top, text='Пересортировать', command=self.resort).pack(side='left', padx=7)
        ttk.Label(top, textvariable=self.summary_text, anchor='e').pack(side='right')

        splitter = ttk.PanedWindow(main, orient='horizontal')
        splitter.pack(fill='both', expand=True, pady=(2, 10))
        left = ttk.Frame(splitter)
        right = ttk.Frame(splitter)
        splitter.add(left, weight=3)
        splitter.add(right, weight=4)

        ttk.Label(left, text='2. Порядок страниц', style='Section.TLabel').pack(anchor='w', pady=(0, 7))
        treebox = ttk.Frame(left)
        treebox.pack(fill='both', expand=True)
        columns = ('n', 'vol', 'file')
        self.tree = ttk.Treeview(treebox, show='headings', columns=columns, selectmode='browse', style='Doc.Treeview')
        self.tree.heading('n', text='№')
        self.tree.heading('vol', text='Том')
        self.tree.heading('file', text='Фотография')
        self.tree.column('n', width=52, minwidth=50, anchor='center', stretch=False)
        self.tree.column('vol', width=52, minwidth=50, anchor='center', stretch=False)
        self.tree.column('file', width=300, minwidth=180)
        scroll = ttk.Scrollbar(treebox, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.tag_configure('vol1', background='#f6f9fc')
        self.tree.tag_configure('vol2', background='#eef8ff')
        self.tree.tag_configure('vol3', background='#f5f4ff')
        self.tree.tag_configure('boundary', foreground='#1358a7')
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        self.tree.bind('<Control-Up>', lambda e: self.move_selected(-1))
        self.tree.bind('<Control-Down>', lambda e: self.move_selected(1))
        self.tree.bind('<Double-Button-1>', lambda e: self.show_enlarged())
        adjust = ttk.Frame(left)
        adjust.pack(fill='x', pady=(8, 3))
        ttk.Button(adjust, text='↑ Вверх', command=lambda: self.move_selected(-1)).pack(side='left', padx=(0, 5))
        ttk.Button(adjust, text='↓ Вниз', command=lambda: self.move_selected(1)).pack(side='left', padx=(0, 5))
        ttk.Button(adjust, text='↻ Перевернуть порядок', command=self.reverse_order).pack(side='left')
        jumps = ttk.Frame(left)
        jumps.pack(fill='x', pady=(6, 5))
        ttk.Label(jumps, text='Перейти к странице №').pack(side='left')
        self.jump_entry = ttk.Entry(jumps, width=7)
        self.jump_entry.pack(side='left', padx=(6, 4))
        self.jump_entry.bind('<Return>', lambda e: self.jump_to())
        ttk.Button(jumps, text='Перейти', command=self.jump_to).pack(side='left')
        ttk.Label(left, text='Номера листов на фотографиях проверяйте по предпросмотру.', foreground='#626d78').pack(anchor='w', pady=(3, 0))

        ttk.Label(right, text='Предпросмотр выбранной страницы', style='Section.TLabel').pack(anchor='w', pady=(0, 7))
        previewpanel = ttk.Frame(right, padding=6)
        previewpanel.pack(fill='both', expand=True)
        self.preview_canvas = tk.Canvas(previewpanel, bg='#e7ebf0', highlightthickness=0)
        self.preview_canvas.pack(fill='both', expand=True)
        self.preview_canvas.bind('<Configure>', lambda e: self._draw_preview())
        ttk.Label(right, textvariable=self.file_title, font=('Segoe UI', 10, 'bold'), wraplength=520).pack(fill='x', pady=(7, 0))
        ttk.Label(right, textvariable=self.file_detail, wraplength=520, foreground='#626d78').pack(fill='x')
        tools = ttk.Frame(right)
        tools.pack(fill='x', pady=(8, 6))
        ttk.Button(tools, text='⟲ На 90° влево', command=lambda: self.rotate_selected(-90)).pack(side='left', padx=(0, 6))
        ttk.Button(tools, text='⟳ На 90° вправо', command=lambda: self.rotate_selected(90)).pack(side='left', padx=(0, 6))
        ttk.Button(tools, text='Увеличить', command=self.show_enlarged).pack(side='left')

        volumes = ttk.LabelFrame(main, text='3. Указать начало второго и третьего томов', padding=(12, 8))
        volumes.pack(fill='x', pady=(0, 10))
        ttk.Button(volumes, text='Выбранная страница = начало ТОМА 2', command=lambda: self.mark_volume(2)).pack(side='left', padx=(0, 9))
        ttk.Button(volumes, text='Выбранная страница = начало ТОМА 3', command=lambda: self.mark_volume(3)).pack(side='left', padx=(0, 9))
        ttk.Button(volumes, text='Сбросить границы', command=self.clear_marks).pack(side='left')
        self.volumes_status = ttk.Label(volumes, text='Границы не указаны')
        self.volumes_status.pack(side='right')

        bottom = ttk.Frame(main)
        bottom.pack(fill='x')
        ttk.Label(bottom, text='4. Качество: ').pack(side='left')
        ttk.Combobox(bottom, values=list(MAX_SIDE_CHOICES), textvariable=self.size_var, width=36, state='readonly').pack(side='left', padx=(3, 14))
        ttk.Label(bottom, text='JPEG:').pack(side='left')
        ttk.Spinbox(bottom, from_=75, to=96, increment=1, textvariable=self.quality_var, width=4).pack(side='left', padx=(4, 16))
        ttk.Checkbutton(bottom, variable=self.combine_var, text='Дополнительно общий PDF').pack(side='left')
        self.export_button = ttk.Button(bottom, text='СОЗДАТЬ 3 PDF', command=self.start_export, style='Accent.TButton')
        self.export_button.pack(side='right')
        self.cancel_button = ttk.Button(bottom, text='Отменить', command=self.cancel_export, state='disabled')
        self.cancel_button.pack(side='right', padx=6)
        self.progress = ttk.Progressbar(main, maximum=100, variable=self.progress_value)
        self.progress.pack(fill='x', pady=(12, 2))
        ttk.Label(main, textvariable=self.status_text, foreground='#58616b').pack(anchor='w', pady=(0, 3))

    def choose_folder(self):
        folder = filedialog.askdirectory(title='Выберите папку со всеми фотографиями')
        if folder:
            self.load_folder(Path(folder))

    def load_folder(self, folder: Path):
        try:
            photos = scan_folder(folder, SORT_CHOICES[self.sort_var.get()])
        except Exception as e:
            messagebox.showerror('Ошибка чтения папки', str(e))
            return
        if not photos:
            messagebox.showwarning('Нет фотографий', 'Не найдены HEIC, HEIF, JPG, PNG и другие поддерживаемые изображения.')
            return
        self.photos = photos
        self.source_dir = folder
        self.tom2 = self.tom3 = None
        self.active_index = None
        self._refresh_tree(0)
        self.status_text.set(f'Загружено фотографий: {len(photos)}. По умолчанию порядок обратный по имени файла.')

    def resort(self):
        if not self.source_dir:
            return
        if (self.tom2 or self.tom3) and not messagebox.askyesno('Изменить порядок?', 'Границы томов будут сброшены при новой сортировке. Продолжить?'):
            return
        self.load_folder(self.source_dir)

    def _positions(self):
        index2 = self.photos.index(self.tom2) if self.tom2 in self.photos else None
        index3 = self.photos.index(self.tom3) if self.tom3 in self.photos else None
        return index2, index3

    def _refresh_tree(self, selected_index: int | None = None):
        old = self.tree.selection()
        if selected_index is None and old:
            selected_index = int(old[0]) if old[0].isdigit() else 0
        self.tree.delete(*self.tree.get_children())
        a, b = self._positions()
        for i, photo in enumerate(self.photos):
            vol = 1 if a is None or i < a else (2 if b is None or i < b else 3)
            if b is not None and a is None:
                vol = 1
            label = photo.path.name
            if self.source_dir and photo.path.parent != self.source_dir:
                label = str(photo.path.relative_to(self.source_dir))
            if photo.rotate:
                label += f'  ↻{photo.rotate}°'
            tags = (f'vol{vol}',)
            if i == a or i == b:
                tags += ('boundary',)
                label = ('▶ НАЧАЛО ТОМA 2  |  ' if i == a else '▶ НАЧАЛО ТОМA 3  |  ') + label
            self.tree.insert('', 'end', iid=str(i), values=(i + 1, vol, label), tags=tags)
        self.summary_text.set(f'{len(self.photos)} фотографий')
        if a is not None and b is not None and 0 < a < b < len(self.photos):
            self.volumes_status.config(text=f'Том 1: {a}  ·  Том 2: {b-a}  ·  Том 3: {len(self.photos)-b}')
        else:
            self.volumes_status.config(text='Укажите начала томов 2 и 3')
        if self.photos:
            selected_index = max(0, min(selected_index or 0, len(self.photos)-1))
            self.tree.selection_set(str(selected_index))
            self.tree.see(str(selected_index))
            self.show_selected()
        else:
            self.preview_canvas.delete('all')

    def selected_index(self) -> int | None:
        item = self.tree.selection()
        if not item:
            return None
        return int(item[0])

    def show_selected(self, event=None):
        index = self.selected_index()
        if index is None or index >= len(self.photos):
            return
        photo = self.photos[index]
        self.active_index = index
        self.file_title.set(f'Страница {index+1} / {len(self.photos)}  ·  {photo.path.name}')
        self.file_detail.set(f'Поворот: {photo.rotate}°  ·  {photo.path}')
        self._draw_preview()

    def _draw_preview(self):
        canvas = self.preview_canvas
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        index = self.active_index
        if index is None or not (0 <= index < len(self.photos)) or width < 30 or height < 30:
            return
        photo = self.photos[index]
        try:
            image = load_photo(photo.path, photo.rotate, max_side=max(width * 2, height * 2, 1400))
            image.thumbnail((width - 16, height - 16), Image.Resampling.LANCZOS)
            display = ImageTk.PhotoImage(image)
            image.close()
            self.active_preview = display
            canvas.create_image(width // 2, height // 2, image=display, anchor='center')
        except Exception as e:
            canvas.create_text(width // 2, height // 2, text=f'Ошибка открытия фото:\n{e}', width=width-40, fill='#b00020')
            self.active_preview = None

    def show_enlarged(self):
        index = self.selected_index()
        if index is None:
            return
        photo = self.photos[index]
        try:
            image = load_photo(photo.path, photo.rotate, max_side=3200)
        except Exception as e:
            messagebox.showerror('Ошибка', str(e))
            return
        win = tk.Toplevel(self.root)
        win.title(f'Предпросмотр страницы {index+1} — {photo.path.name}')
        win.geometry('1050x790')
        toolbar = ttk.Frame(win, padding=8)
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='Колёсико мыши / полосы прокрутки: просмотр · Масштаб:').pack(side='left')
        scale = tk.DoubleVar(value=1.0)
        box = ttk.Frame(win)
        box.pack(fill='both', expand=True)
        canvas = tk.Canvas(box, bg='#d8dde4')
        vscroll = ttk.Scrollbar(box, orient='vertical', command=canvas.yview)
        hscroll = ttk.Scrollbar(box, orient='horizontal', command=canvas.xview)
        canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        vscroll.pack(side='right', fill='y')
        hscroll.pack(side='bottom', fill='x')
        canvas.pack(side='left', fill='both', expand=True)
        cache = {'image': None}

        def redraw(value=None):
            factor = float(scale.get())
            target = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
            pic = image.resize(target, Image.Resampling.LANCZOS) if target != image.size else image
            cache['image'] = ImageTk.PhotoImage(pic)
            canvas.delete('all')
            canvas.create_image(0, 0, image=cache['image'], anchor='nw')
            canvas.configure(scrollregion=(0, 0, *target))
            if pic is not image:
                pic.close()

        ttk.Scale(toolbar, variable=scale, from_=0.25, to=1.6, orient='horizontal', command=redraw, length=210).pack(side='left', padx=8)
        ttk.Button(toolbar, text='Закрыть', command=win.destroy).pack(side='right')
        def on_wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), 'units')
        canvas.bind('<MouseWheel>', on_wheel)
        def release_image():
            image.close()
            win.destroy()
        win.protocol('WM_DELETE_WINDOW', release_image)
        redraw()

    def move_selected(self, delta):
        index = self.selected_index()
        if index is None or not 0 <= index + delta < len(self.photos):
            return
        self.photos[index], self.photos[index+delta] = self.photos[index+delta], self.photos[index]
        self._refresh_tree(index+delta)

    def reverse_order(self):
        if not self.photos:
            return
        if (self.tom2 or self.tom3) and not messagebox.askyesno('Обратный порядок', 'Границы томов будут сброшены. Продолжить?'):
            return
        self.photos.reverse()
        self.tom2 = self.tom3 = None
        self._refresh_tree(0)
        self.status_text.set('Порядок фотографий полностью перевёрнут.')

    def rotate_selected(self, delta):
        index = self.selected_index()
        if index is None:
            return
        item = self.photos[index]
        item.rotate = (item.rotate + delta) % 360
        self._refresh_tree(index)

    def jump_to(self):
        try:
            number = int(self.jump_entry.get())
            if number < 1 or number > len(self.photos):
                raise ValueError
        except ValueError:
            messagebox.showwarning('Неверный номер', f'Введите число от 1 до {len(self.photos)}.')
            return
        self.tree.selection_set(str(number - 1))
        self.tree.see(str(number - 1))
        self.show_selected()

    def mark_volume(self, number: int):
        index = self.selected_index()
        if index is None:
            return
        if number == 2:
            self.tom2 = self.photos[index]
        else:
            self.tom3 = self.photos[index]
        self._refresh_tree(index)
        a, b = self._positions()
        if a is not None and b is not None and not 0 < a < b < len(self.photos):
            messagebox.showwarning('Проверьте порядок томов', 'Начало тома 2 должно быть раньше начала тома 3, и первый том не должен быть пустым.')

    def clear_marks(self):
        self.tom2 = self.tom3 = None
        self._refresh_tree(self.selected_index())

    def save_project(self):
        if not self.photos or not self.source_dir:
            messagebox.showinfo('Нет фотографий', 'Сначала выберите папку с фотографиями.')
            return
        filename = filedialog.asksaveasfilename(title='Сохранить порядок и границы томов', defaultextension='.json', filetypes=[('Проект JSON', '*.json')], initialfile='Порядок_томов.json')
        if not filename:
            return
        data = {
            'version': 1, 'source_dir': str(self.source_dir),
            'photos': [{'path': str(p.path.relative_to(self.source_dir)), 'rotate': p.rotate} for p in self.photos],
            'tom2': str(self.tom2.path.relative_to(self.source_dir)) if self.tom2 else None,
            'tom3': str(self.tom3.path.relative_to(self.source_dir)) if self.tom3 else None,
        }
        try:
            Path(filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.status_text.set('Проект сохранён: ' + filename)
        except Exception as e:
            messagebox.showerror('Ошибка сохранения', str(e))

    def open_project(self):
        filename = filedialog.askopenfilename(title='Открыть сохранённый проект', filetypes=[('Проект JSON', '*.json')])
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding='utf-8'))
            folder = Path(data['source_dir'])
            photos = [Photo(folder / item['path'], int(item.get('rotate', 0)) % 360) for item in data['photos']]
            if not photos or any(not p.path.is_file() for p in photos):
                raise ValueError('Не найдены исходные фотографии. Верните их в исходную папку.')
            self.source_dir = folder
            self.photos = photos
            self.tom2 = next((p for p in photos if str(p.path.relative_to(folder)) == data.get('tom2')), None)
            self.tom3 = next((p for p in photos if str(p.path.relative_to(folder)) == data.get('tom3')), None)
            self._refresh_tree(0)
            self.status_text.set('Проект восстановлен: ' + filename)
        except Exception as e:
            messagebox.showerror('Не удалось открыть проект', str(e))

    def start_export(self):
        if self.exporting:
            return
        a, b = self._positions()
        if a is None or b is None or not 0 < a < b < len(self.photos):
            messagebox.showwarning('Укажите границы томов', 'Выберите первую страницу тома 2 и нажмите «Начало ТОМА 2». Затем таким же способом отметьте начало тома 3.')
            return
        target = filedialog.askdirectory(title='Куда сохранить готовые PDF?')
        if not target:
            return
        output_dir = Path(target)
        targets = [output_dir / f'Материалы_дела_Том_{i}.pdf' for i in (1, 2, 3)]
        if self.combine_var.get():
            targets.append(output_dir / 'Материалы_дела_Все_3_тома.pdf')
        existing = [p.name for p in targets if p.exists()]
        if existing and not messagebox.askyesno('Файлы уже существуют', 'Следующие PDF будут перезаписаны:\n' + '\n'.join(existing) + '\n\nПродолжить?'):
            return
        if not messagebox.askyesno('Проверка перед созданием', f'Всего фотографий: {len(self.photos)}\nТом 1: {a} страниц\nТом 2: {b-a} страниц\nТом 3: {len(self.photos)-b} страниц\n\nПроверьте, что видимые номера листов идут по порядку. Программа не распознаёт номера автоматически.\n\nСоздать PDF?'):
            return
        try:
            quality = int(self.quality_var.get())
            if not 75 <= quality <= 96:
                raise ValueError('Качество JPEG должно быть от 75 до 96.')
        except Exception as e:
            messagebox.showerror('Неверные настройки качества', str(e))
            return
        self.exporting = True
        self.cancel_event.clear()
        self.progress_value.set(0)
        self.export_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.status_text.set('Генерация PDF...')
        # Snapshot: user can't accidentally affect a running export by changing the table.
        snapshot = [Photo(p.path, p.rotate) for p in self.photos]
        max_side = MAX_SIDE_CHOICES[self.size_var.get()]
        combine = self.combine_var.get()
        def worker():
            try:
                output = create_pdfs(
                    snapshot, a, b, output_dir, max_side=max_side, quality=quality, combine=combine,
                    progress=lambda done, total, title: self.task_queue.put(('progress', done, total, title)),
                    is_cancelled=self.cancel_event.is_set,
                )
                self.task_queue.put(('done', output))
            except InterruptedError as exc:
                self.task_queue.put(('cancelled', str(exc)))
            except Exception as exc:
                self.task_queue.put(('error', str(exc) + '\n\n' + traceback.format_exc()))
        threading.Thread(target=worker, daemon=True).start()

    def cancel_export(self):
        if self.exporting:
            self.cancel_event.set()
            self.status_text.set('Запрошена отмена. Завершаю текущую операцию...')
            self.cancel_button.configure(state='disabled')

    def _poll(self):
        try:
            while True:
                item = self.task_queue.get_nowait()
                kind = item[0]
                if kind == 'progress':
                    _, done, total, title = item
                    self.progress_value.set(100 * done / total if total else 0)
                    self.status_text.set(f'{done}/{total}  ·  {title}')
                elif kind == 'done':
                    self._finish_export()
                    output = item[1]
                    self.status_text.set('Готово! Создано файлов: ' + str(len(output)))
                    messagebox.showinfo('PDF созданы', 'Готово!\n\n' + '\n'.join(str(p) for p in output))
                    if output and messagebox.askyesno('Открыть папку?', 'Открыть папку с готовыми PDF?'):
                        try:
                            os.startfile(str(output[0].parent))
                        except AttributeError:
                            pass
                elif kind == 'cancelled':
                    self._finish_export()
                    self.status_text.set(item[1])
                    messagebox.showinfo('Отменено', item[1])
                elif kind == 'error':
                    self._finish_export()
                    self.status_text.set('Ошибка при создании PDF')
                    messagebox.showerror('Ошибка создания PDF', item[1][:4000])
        except queue.Empty:
            pass
        self.root.after(200, self._poll)

    def _finish_export(self):
        self.exporting = False
        self.export_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')


def main():
    root = tk.Tk()
    ReaderApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
