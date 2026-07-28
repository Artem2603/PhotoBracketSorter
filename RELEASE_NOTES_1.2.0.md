# Photo Bracket Sorter 1.2.0 — Release Notes

Date: 2026-07-28
Previous stable release: 1.1.1 (207 tests). This release: 527 tests, 0 failures.

## На русском

### Что нового

1. **Автоматическая настройка анализа** — сканирует папку и предлагает
   настройки (режим брекетинга, допуски по времени/экспозиции) с явным
   диалогом «Применить / Изменить / Отмена». Ничего не меняется без
   подтверждения.
2. **Очередь ручной проверки** — отдельный экран для «пограничных»
   результатов: подтвердить HDR3/5, выбрать другой размер серии, отметить
   «не брекетинг», разделить, объединить с соседними кадрами, исключить,
   вернуть автоматическое решение.
3. **Универсальные профили камер** — создание, редактирование, импорт и
   экспорт профилей (JSON, с версией схемы), включая создание профиля из
   уже подтверждённой серии.
4. **«Почему так определено?»** — объяснение решения алгоритма на основе
   реальных данных детектора, без повторного расчёта «на глазок».
5. **Визуальный просмотр серий** — предпросмотр RAW-кадров серии с
   синхронным зумом/панорамированием, режимом «мигания», гистограммой и
   предупреждениями о пересвете/провале теней.
6. **Предпросмотр результата сортировки** — дерево папок результата до
   нажатия Copy/Move, с интерактивными правками (исключить, изменить
   категорию, переименовать серию), которые никогда не трогают файлы
   напрямую.
7. **Журнал операций и отмена перемещения** — каждый Copy/Move пишет запись
   в постоянный журнал; отмена перемещения доступна только если файл
   результата на месте, его SHA-256 совпадает, а исходный путь свободен.
8. **Расширенная классификация** — HDR3/5/7/9, неполный брекетинг,
   фокус-брекетинг, серийная съёмка, вероятная панорама, пары RAW+JPEG/HEIC,
   одиночные кадры. Панорама **никогда** не перемещается агрессивно
   автоматически — это только информационная метка с собственным порогом
   и переключателем на каждый детектор.
9. **Сохранение сессии (.pbsession) и кеш EXIF** — сохраняет пути, настройки,
   отпечаток папки, извлечённые метаданные (чтобы не гонять ExifTool
   повторно) и все ручные правки (серии, категории, снятые галочки,
   отклонённые кандидаты). Сами фотографии в файл сессии никогда не
   попадают. Повреждённый файл сессии не приводит к сбою и не теряет файлы.
10. **Новый режим интерфейса: Простой / Расширенный** — простой режим
    оставляет ровно 5 шагов (папка источника, папка результата, Анализ,
    просмотр таблицы, Copy/Move), расширенный — весь функционал без
    изменений. Также начат постепенный разбор `main_window.py` на
    отдельные модули (пока перенесены диалоги сессии и диалог настроек —
    подробности в CHANGELOG.md).

### Известные ограничения

- Интерактивная проверка кликами в реальном окне не проводилась в этой
  среде (ограничение инструмента, не приложения) — см. TEST_REPORT_1.2.0.md.
- Ни одна функция не проверялась на реальных RAW-файлах с настоящей
  камеры — только на синтетических тестовых данных.
- Разбор `main_window.py` на модули выполнен частично (2 из 6
  запланированных файлов); подробности — в CHANGELOG.md.

## In English

### What's new

1. **Automatic analysis configuration** — scans the folder and suggests
   settings (bracket size mode, time/exposure tolerances) behind an
   explicit Apply/Modify/Cancel dialog. Nothing changes without
   confirmation.
2. **Manual review queue** — a dedicated screen for borderline results:
   confirm HDR3/5, pick a different series size, mark "not a bracket",
   split, merge with neighboring frames, exclude, or restore the
   automatic decision.
3. **Universal camera profiles** — create, edit, import, and export
   profiles (schema-versioned JSON), including creating a profile straight
   from an already-confirmed series.
4. **"Why was this decided?"** — explains the algorithm's decision from the
   real detector data, never a re-derived approximation.
5. **Visual series viewer** — embedded RAW preview with synced zoom/pan, a
   blink mode, a histogram, and highlight/shadow clipping warnings.
6. **Sort-result preview** — the destination folder tree before Copy/Move
   runs, with interactive edits (exclude, recategorize, rename a series)
   that never touch files directly.
7. **Operation journal and Move undo** — every Copy/Move writes a
   persistent journal entry; undoing a Move is only offered when the
   destination file still exists, its SHA-256 still matches, and the
   original path is free.
8. **Extended classification** — HDR3/5/7/9, incomplete bracket, focus
   bracket, burst, probable panorama, RAW+JPEG/HEIC pairs, singles.
   Panorama is **never** aggressively auto-moved — it is an informational
   label only, with its own threshold and its own on/off switch.
9. **Session save (.pbsession) and an EXIF cache** — saves paths, settings,
   the folder fingerprint, already-extracted metadata (so ExifTool doesn't
   need to re-run), and every manual fix (series, category overrides,
   deselections, dismissed candidates). The photos themselves are never
   written to the session file. A corrupted session file cannot crash the
   app or lose files.
10. **New Simple/Advanced UI mode** — simple mode leaves exactly 5 steps
    (source folder, output folder, Analyze, review the table, Copy/Move);
    advanced mode is the full interface, unchanged. Also started a gradual
    split of `main_window.py` into separate modules (session dialogs and
    the settings dialog moved so far — see CHANGELOG.md for detail).

### Known limitations

- Interactive click-through verification in a live window was not possible
  in this environment (a tooling limitation, not an application defect) —
  see TEST_REPORT_1.2.0.md.
- No feature was verified against real camera RAW files — only synthetic
  test fixtures.
- The `main_window.py` module split is partial (2 of 6 planned files) —
  see CHANGELOG.md for what's done and what remains.

## Deliverables

- `PhotoBracketSorter_Project_1.2.0.zip` — full source, tests, and docs.
- `PhotoBracketSorter-1.2.0-portable.zip` — ready-to-run portable build
  (extract and run `PhotoBracketSorter.exe`).
- `PhotoBracketSorter.exe` — a loose copy of the executable for reference/
  hash-verification only; it depends on the `_internal` folder next to it
  and will not run on its own outside the portable zip.
- `SHA256SUMS.txt` — checksums for the three files above.
- `CHANGELOG.md`, `RELEASE_NOTES_1.2.0.md`, `TEST_REPORT_1.2.0.md`.
