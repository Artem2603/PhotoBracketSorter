# Аудит PhotoBracketSorter 1.1.0

Дата: 2026-07-22
Базовое состояние: 80 тестов, все проходят (`PYTHONPATH=. py -m unittest discover -s tests -t .`).
Окружение: Python 3.13.5, `python` в PATH — это заглушка WindowsApps, работать нужно через `py`. `pytest` не установлен.
Репозиторий: **не под git** — отката нет, изменения делаются осторожно.

Легенда: 🔴 подтверждено экспериментально · 🟠 подтверждено чтением кода

---

## ✅ Статус: все пункты исправлены в 1.1.1

Документ сохранён как отчёт о найденном. Что сделано по каждому пункту — в
`CHANGELOG.md`, раздел 1.1.1. Итог: **207 тестов** (было 80), все проходят.

| Пункт | Статус | Где проверяется |
|---|---|---|
| P0-1 экспозиция | исправлено | `tests/test_exposure_sources.py` (22) |
| P0-2 мёртвые настройки | все 21 подключены | `tests/test_settings_are_wired.py` (31) |
| P0-3 ручные серии | исправлено | `tests/test_series_manager.py` (22), `tests/test_gui_manual_series.py` (8) |
| P0-4 Copy / Move | исправлено | `tests/test_transfer_safety.py` (20) |
| P1-5 Unicode pairing | исправлено | `tests/test_pairing_p1.py` (24) |
| P1-6 TIFF | исправлено | `tests/test_pairing_p1.py` |
| P1-7 privacy_redact | исправлено | `tests/test_transfer_safety.py` |
| P1-8 производительность | 5000×5000 за 0.42 с | `tests/test_pairing_p1.py` |
| Зависимости, сборка | закреплены, `SPECPATH` | `BUILD.md` |
| Разделение `main_window.py` | **предложено, не выполнено** | см. ниже |

---

## P0-1. Экспозиция: смешение источников и слепота к реальным брекетам

### 1.1 🔴 Разные источники сравниваются между собой
`app/exposure.py:39-50` — `exposure_values()` собирает значение для каждого файла
**независимо**, затем склеивает названия источников через `"+".join(...)`.

Проверено:

```
mixed = [EC=-2.0], [BaselineExposure=0.5], [ISO/выдержка/диафрагма]
exposure_values(mixed) -> ([-2.0, 0.5, 8.906890595608518],
                           'BaselineExposure+EV100+ExposureCompensation')
```

Значения −2.0, 0.5 и 8.9 попадают в один список и дальше идут в
`bracket_detection.py:143-152` — в расчёт `span` и `roughly_uniform_steps()`.
Это сравнение килограммов с километрами. Результат: ложный «огромный разброс
экспозиции» → мусорные серии, либо ложный «неравномерный шаг» → пропуск серии.

### 1.2 🔴 EC=0 у всех кадров маскирует настоящий брекет
`app/exposure.py:11-12` — `exposure_compensation` имеет безусловный высший
приоритет. Многие камеры (в т.ч. телефоны в ручном/AEB-режиме) пишут
`ExposureCompensation=0` во все кадры серии, а вилку делают выдержкой.

Проверено на настоящей HDR3-вилке −2/0/+2 EV (выдержки 1/500, 1/125, 1/30 при
f/2.8 ISO 100):

```
exposure_values(files) -> ([0.0, 0.0, 0.0], 'ExposureCompensation')
span = 0.0   # реальный span = 4.0 EV
```

`span (0.0) >= min_exposure_span_ev (0.7)` → False, `roughly_uniform_steps`
даёт `mean_step = 0` → False. Серия теряет 45 баллов из 100 и **никогда не
проходит порог** 72.0. Настоящая HDR-вилка молча не распознаётся — ровно то,
ради чего программа написана.

### 1.3 🟠 Смешение семантик
Источники физически несовместимы и не приводятся к общей шкале:
- `ExposureCompensation` — сдвиг относительно замера (отн.)
- `BaselineExposure` — DNG-тег обработки (отн., другая база)
- `LightValue` / `BrightnessValue` — яркость сцены (растёт со светом)
- `EV100` — экспозиция камеры (растёт с **уменьшением** света)

`LightValue` и `EV100` имеют **противоположный знак** относительно яркости
сцены. Сортировка кадров по такому «EV» даёт обратный порядок.

**Исправление:** выбирать **один** источник на всю группу; использовать его
только если он доступен у **всех** файлов; иначе — переходить к следующему.
Приоритет должен ставить EV100 выше, чем вырожденный (все нули) EC.
Никогда не смешивать. Плюс тесты: настоящая HDR3, настоящая HDR5, EC=0 при
разных выдержках.

---

## P0-2. Мёртвые настройки (21 шт.)

Проверено grep-ом: перечисленные ниже поля встречаются в `app/` **только** в
строке регистрации виджета в диалоге настроек `gui/main_window.py:822-878`
(плюс `models.py`/`settings.py` — хранение и валидация). Ни одна не читается
логикой. Они сохраняются в JSON, валидируются, показываются пользователю — и
не делают ничего.

| Настройка | UI | Что происходит на самом деле |
|---|---|---|
| `use_name_for_pairing` | :846 | имя **всегда** учитывается — `pairing.py:107` |
| `use_exif_for_pairing` | :847 | EXIF **всегда** учитывается — `pairing.py:89-106` |
| `use_preview_for_pairing` | :848 | превью **никогда** не используется; `RawHeicPair.preview_similarity` жёстко `None` (`pairing.py:56`) |
| `max_bracket_duration_seconds_3` | :838 | схлопывается в `max(3,5)` (`:935-938`); детектор берёт только общий `max_bracket_duration_seconds` (`bracket_detection.py:99`) |
| `max_bracket_duration_seconds_5` | :839 | то же — раздельных лимитов нет |
| `min_auto_confidence` | :843 | порог **захардкожен** 82/72/62 в `bracket_detection.py:226-231` |
| `excluded_folder_names` | :833 | **захардкожен** `SORTED_ROOT_PREFIX` в `analysis.py:72` |
| `preserve_source_subfolders` | :856 | `copy_plan.py` всегда кладёт плоско |
| `duplicate_behavior` | :857 | всегда `rename` — `collision_handling.py:8`; `skip`/`overwrite` не реализованы |
| `output_folder_template` | :858 | **захардкожен** в `paths.py:36-37` |
| `preserve_file_times` | :859 | `shutil.copy2` сохраняет времена **всегда**, снять галочку нельзя |
| `preview_only` | :860 | ⚠️ **опасно**: галочка «только предпросмотр» не мешает копированию/перемещению. Пользователь ждёт dry-run — получает реальные операции с файлами |
| `preview_similarity_enabled` | :868 | сравнения превью нет в коде |
| `visual_similarity_threshold` | :869 | то же |
| `report_formats` | :871 | `reporting.py:22-26` всегда пишет json+csv+txt |
| `report_language` | :872 | отчёты всегда по-русски (`reporting.py:113-121`) |
| `csv_english_headers` | :874 | заголовки CSV всегда английские (`reporting.py:89`), переключателя нет |
| `exiftool_path` | :877 | `MetadataReader` создаёт `ExifToolRunner()` без аргументов (`metadata.py:23`) — путь игнорируется |
| `exiftool_batch_size` | :867 | `chunk_size=80` **захардкожен** в `exiftool_runner.py:108` |
| `worker_count` | :864 | чтение метаданных строго последовательное (`metadata.py:36`) |
| `log_level` | :878 | `configure_logging` жёстко ставит `level=logging.INFO` (`logging_setup.py:18`) |

Сверх списка пользователя — те же симптомы: `report_detail` (:873),
`mixed_timezone_safe_mode` (:853), `require_plan_before_operation` (:861),
`column_widths` / `column_order` (0 обращений вообще),
`preview_cache_items` (:866 — `PreviewCache` создаётся в `main_window.py:57`,
но `_update_preview` (:626) зовёт `load_tk_preview` напрямую, мимо кэша).

---

## P0-3. Ручные серии

### 3.1 🔴 Одинаковый ID у всех ручных серий
`gui/main_window.py:716`:

```python
series_id = f"MANUAL_HDR{size}_{len(files):04d}"
```

`len(files)` здесь **всегда равен `size`** — это гарантирует проверка строкой
выше (`:711-713` требует выбрать ровно `size` файлов). Проверено:

```
HDR3 серия #1 -> MANUAL_HDR3_0003
HDR3 серия #2 -> MANUAL_HDR3_0003
HDR3 серия #3 -> MANUAL_HDR3_0003
```

Все ручные HDR3 получают идентичный ID, все HDR5 — идентичный. Дальше
`copy_plan.py:31` кладёт их в один каталог `BRACKETING_3/MANUAL_HDR3_0003/RAW/`,
и разные серии **сливаются в одну папку**.

### 3.2 🟠 Нет проверки форматов
`_manual_series` (:710) проверяет только количество. Можно выделить 3 HEIC/JPEG
(или битые файлы) и объявить их RAW-брекетом — они уедут в `.../RAW/`.

### 3.3 🟠 Ручные серии не попадают в общий анализ
`_manual_series` правит только поля `PhotoFile`. Объект `BracketSeries` не
создаётся, в `analysis.bracket_series` не добавляется. Следствия:
- `reporting.py:22` не пишет для них `series.json`;
- `_series_to_dict` (:184) их не видит — в `summary.json` ручных серий нет;
- `series.destination_root` отсутствует.

### 3.4 🟠 Расхождение GUI и отчётов
Два независимых счётчика статистики с **разным набором ключей**:
`classification.py:101` (`_statistics`) и `main_window.py:971`
(`_recount_statistics`). Второй теряет `raw_in_series_3`, `raw_in_series_5`,
`companions_in_series`, `bytes_planned` считает по другому правилу
(без учёта `selected`), а `supported` определяет иначе. После любой ручной
правки GUI показывает одни числа, отчёт — другие.

### 3.5 🟠 Нет единого владельца состояния
Серии живут в трёх местах сразу: `PhotoFile.series_id`,
`AnalysisResult.bracket_series`, `series.destination_root`. Синхронизации нет.
`_dissolve_series` (:737) чистит `file.series_id`, но осиротевший
`BracketSeries` остаётся в `analysis.bracket_series` и попадает в отчёт.

---

## P0-4. Copy / Move

### 4.1 🟠 Проверка изменения файла неполная
`copy_engine.py:82-84` и `move_engine.py:95-97` сверяют **только размер**.
Правка файла без изменения размера (перезапись EXIF, коррекция в редакторе)
не ловится — при Move оригинал будет удалён после копирования уже другого
содержимого. Нужны mtime + быстрый fingerprint (`integrity.quick_fingerprint`
уже написан на `integrity.py:20` и **нигде не используется**).

### 4.2 🟠 Отчёт врёт после Move
`reporting.py:121` — строка `"Оригиналы не изменены."` пишется **безусловно**,
в том числе после Move, который оригиналы удалил. Прямая дезинформация о
необратимой операции. `write_reports` вообще не знает, Copy это был или Move
(`main_window.py:393` зовёт её одинаково).

### 4.3 🟠 При отмене отчёта нет вообще
`copy_engine.py:44` и `move_engine.py:46,52` при отмене бросают
`CopyCancelled`/`MoveCancelled`. Исключение улетает в
`_transfer_worker` (:395) → ветка `("error", exc)`, а `write_reports` (:393)
**не вызывается**. После отмены Move часть файлов уже перемещена, часть
оригиналов удалена — и никакого следа о том, что именно произошло.
Поле `CopyResult.cancelled` существует, но всегда `False` (`:60`, `:74`).

### 4.4 🟠 Анализ не сбрасывается после Move
`_poll_events` → `transfer_done` (:461-472) не трогает `self.analysis`.
Кнопки Copy/Move остаются активными (`_set_busy(False)` → `:491-493`).
Можно нажать Move второй раз по тому же плану — исходников уже нет, все
операции упадут в `FileNotFoundError`. Плюс `analysis.fingerprint` описывает
уже несуществующие файлы.

### 4.5 🟠 `analysis_fingerprint` не перепроверяется
`CopyPlan.analysis_fingerprint` заполняется (`copy_plan.py:58`), но **никто
никогда не сверяет** его перед выполнением. Устаревший план выполнится молча.

### 4.6 🟠 Ошибки не доходят до GUI
`operation.error` и `copy_status`/`copy_error` в `PhotoFile` не связаны:
`PhotoFile.copy_status` (`models.py:91`) не обновляется **никогда**.
Фильтр «errors» (`main_window.py:593`) по `file.copy_error` всегда пуст.

---

## P1-5. RAW + HEIC pairing

### 5.1 🔴 Кириллица уничтожается, давая ложные совпадения
`pairing.py:128-132` — `re.sub(r"[^a-z0-9]+", "", stem)` вырезает всё, кроме
латиницы и цифр. Проверено:

```
normalize_stem('Отпуск_снимок.dng')  -> ''
normalize_stem('Свадьба_кадр.heic')  -> ''
stem_similarity(...)                 -> 1.0   # ИДЕАЛЬНОЕ совпадение
```

`SequenceMatcher("", "").ratio()` возвращает 1.0. Два **никак не связанных**
файла с кириллическими именами получают максимальный бонус
`+10.0 * 1.0` и `PAIR_NAME_MATCH` (`:107-110`). Затрагивает любые нелатинские
имена (кириллица, CJK, греческий) и эмодзи.

### 5.2 🟠 Нет Unicode-нормализации
`unicodedata.normalize` не вызывается нигде в проекте. macOS отдаёт имена в
NFD, Windows — в NFC; `IMG_Ё.dng` в двух формах не совпадёт побайтово.

### 5.3 🟠 Неоднозначность помечается, но не различается
`pairing.py:36-40`: при `ambiguous` пара **молча отбрасывается** и файлу
пишется предупреждение. Но `RawHeicPair.ambiguous` при создании всегда
жёстко `False` (`:58`) — флаг в модели мёртвый, до отчёта неоднозначность
не доходит. Также проверка «второго лучшего» есть только со стороны RAW
(`_second_score_for_raw`, :187) — со стороны HEIC симметричной нет.

---

## P1-6. TIFF считается RAW всегда

`format_detection.py:53` — `is_raw_type()` включает `DetectedType.TIFF`
безусловно. Обычный TIFF, экспортированный из Lightroom/Photoshop, — это
**результат обработки**, а не RAW. Последствия: экспортированные TIFF идут в
`RAW/`, участвуют в брекет-детекте как RAW-кадры и не могут стать
rendered-компаньоном в паре (`pairing.py:67` их отсекает).

Отличать настоящий RAW TIFF можно по уже читаемым полям (`models.py:64-71`,
все заполняются в `metadata.py:121-128`, но **не используются**):
`DNGVersion`, `PhotometricInterpretation` (CFA=32803), `Compression`,
`BitsPerSample` (>8), `SamplesPerPixel`, `ActiveArea`, `DefaultCropSize`,
`Software` (признак экспорта).

---

## P1-7. privacy_redact пропускает абсолютные пути

При `privacy_redact_absolute_paths=True` утечки остаются:

| Где | Что утекает |
|---|---|
| `reporting.py:75` | `"settings": analysis.settings_snapshot` — целиком, вместе с `last_source_path`, `last_output_path`, `exiftool_path` |
| `reporting.py:68` | `"output_root": str(copy_result.output_root)` — никогда не редактируется |
| `reporting.py:94` | CSV: `destination_path.relative_to(...)` — ок, но `output_root` в summary остаётся |

`settings_snapshot` (`models.py:342-344, 385`) содержит три поля с
абсолютными путями. Флаг приватности их не трогает — самая крупная утечка,
потому что снимок настроек пишется целиком одним куском.

---

## P1-8. Производительность pairing — O(N×M)

`pairing.py:16-20` — полный декартов проход:

```python
for raw_index, raw_file in enumerate(raw_files):
    for rendered_index, rendered_file in enumerate(rendered_files):
        score, ... = self._score_pair(...)
```

На 5000 RAW + 5000 preview это **25 000 000** вызовов `_score_pair`, каждый из
которых внутри делает `safe_file_time_difference_seconds` (с логированием,
`time_compare.py:207`) и `stem_similarity` → `SequenceMatcher.ratio()`.
`ratio()` сам по себе O(n²) по длине имени. Практически — часы работы и
гигабайты в `candidates` (25M кортежей со списками).

Плюс скрытая проблема: `_second_score_for_raw` (:187) для **каждой** пары
линейно сканирует весь словарь кандидатов → ещё O(N²·M).

**Исправление:** индексация по времени + сортировка, скользящее окно
±`max_pair_delta_seconds`, сравнение только кандидатов внутри окна.
Файлы без времени — отдельная ветка по имени.

Аналогично `bracket_detection.py:25-30`: окна по 3 и 5 строятся по всем
позициям, для каждой считается полный скоринг, `_select_non_overlapping`
(:209) вызывает `.resolve()` (обращение к ФС!) внутри цикла отбора.

---

## Дополнительно

### Прочие дефекты
- `analysis.py:74` — фильтр по расширению применяется **до** определения
  формата, т.е. `allowed_extensions` работает по имени, хотя весь смысл
  `format_detection` — не доверять расширению.
- `copy_engine.py:59` / `move_engine.py:75` — `skipped_duplicates` всегда 0,
  поле в отчёте всегда врёт.
- `copy_engine.py:72` — `mkdir(exist_ok=False)` упадёт, если каталог создан
  между `make_unique_output_root` и запуском (гонка).
- `main_window.py:942` — при ошибке валидации откат
  `settings_from_dict(snapshot)`, но `self.i18n`/тема уже могли смениться.
- `main_window.py:626` — `load_tk_preview` вызывается **синхронно в UI-потоке**:
  на большом RAW интерфейс замирает.
- `main_window.py:568` — `_sort_key` для времени сравнивает ISO-строки, смешивая
  aware и naive (`.replace(tzinfo=None)`) — порядок при смешанных TZ неверен.
- `capture_time.py:42-44` — `path.stat()` вызывается дважды подряд.
- `signatures.py:64` — разбор `compatible_brands` идёт по фиксированным
  смещениям без учёта длины бокса `ftyp`.

### main_window.py — безопасное разделение (предложение, НЕ выполнено)

Сознательно отложено. Задача требовала «не ломать существующий UI без
необходимости», а перемещение ~490 строк GUI-кода в одном заходе с правками
логики смешало бы в одном диффе поведенческие изменения и механический перенос —
и любая регрессия стала бы неразличимой. Разделение стоит делать отдельным
шагом, когда 1.1.1 подтверждена на реальных данных.

Ниже — план на момент аудита (номера строк относятся к версии 1.1.0).
Файл содержит 5 несвязанных обязанностей. Предлагаемое разбиение **без
изменения поведения** (чистый вынос кода, `MainWindow` остаётся фасадом):

| Новый модуль | Что выносим | Строки |
|---|---|---|
| `gui/settings_dialog.py` | `_open_settings`, `_settings_*`, `_apply_settings_dialog`, import/export/reset | 799-969 (~170) |
| `gui/file_table.py` | `_populate_table`, `_sort_key`, `_sort_by`, `_matches_filter`, `_type_icon` | 517-596, 1039-1044 (~90) |
| `gui/preview_panel.py` | `_update_preview`, `_show_metadata_dialog` | 620-668, 783-797 (~65) |
| `gui/manual_actions.py` | `_assign_selected`, `_manual_series`, `_dissolve_series`, `_set_category` | 689-767 (~80) |
| `gui/transfer_controller.py` | `_start_transfer`, `_transfer_worker`, `_confirm_copy`, `_confirm_move` | 356-439 (~85) |

Остаток `main_window.py` ≈ 480 строк — компоновка, события, статус.
Рекомендую делать это **после** P0/P1, отдельным шагом, чтобы правки логики
и перемещение кода не смешивались в одном диффе.

### Зависимости не закреплены
`requirements.txt`: `Pillow>=10`, `ttkbootstrap>=1.10`;
`requirements-dev.txt`: `pytest>=8`, `pyinstaller>=6`, `ruff>=0.8`, `black>=24`;
`pyproject.toml:7-9` — то же. Любая новая мажорная версия Pillow ломает сборку
без предупреждения. Нужны `==` с точными версиями (+ хеши, если нужен
максимум воспроизводимости).

### PyInstaller build не воспроизводим
- `PhotoBracketSorter.spec:6` — `project = Path.cwd()`: сборка зависит от
  текущего каталога, из другого места пути к `profiles`/`assets`/`vendor`
  будут неверными. Нужно `Path(SPECPATH)`.
- `build.bat:3-4` — `pip install` без пинов и без venv, ставит в системный
  Python.
- Нет `PYTHONHASHSEED`/`SOURCE_DATE_EPOCH`, нет фиксации версии PyInstaller.
- `SHA256SUMS.txt` есть, но при неповторяемой сборке хеш не воспроизводится.

---

## План работ

1. **P0-1** экспозиция: единый источник на группу + EV100-фоллбэк при
   вырожденном EC. Тесты: HDR3, HDR5, EC=0 с разными выдержками.
2. **P0-2** настройки: подключить реализуемые, убрать из UI нереализуемые.
   `preview_only` — приоритет (риск потери данных).
3. **P0-3** ручные серии: UUID, проверка форматов, `SeriesManager` как
   единственный владелец, единая статистика.
4. **P0-4** Copy/Move: mtime+fingerprint, честный текст отчёта, частичный
   отчёт при отмене, сброс анализа после Move, сверка fingerprint.
5. **Тесты** после P0.
6. **P1-5…8**: Unicode-нормализация, TIFF, privacy_redact, индексация pairing.
7. **Финал**: пины зависимостей, `SPECPATH` в spec, разделение `main_window.py`,
   CHANGELOG, пересборка архива.
