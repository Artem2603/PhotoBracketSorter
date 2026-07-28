"""Application exception hierarchy."""


class PhotoBracketSorterError(Exception):
    default_user_message = "Ошибка Photo Bracket Sorter."

    def __init__(self, user_message: str | None = None, technical_reason: str | None = None):
        self.user_message = user_message or self.default_user_message
        self.technical_reason = technical_reason or ""
        message = self.user_message
        if self.technical_reason:
            message = f"{message} {self.technical_reason}"
        super().__init__(message)


class ConfigurationError(PhotoBracketSorterError):
    default_user_message = "Некорректная конфигурация."


class ResourceNotFoundError(PhotoBracketSorterError):
    default_user_message = "Не найден необходимый ресурс."


class ExifToolError(PhotoBracketSorterError):
    default_user_message = "ExifTool завершился с ошибкой."


class ExifToolTimeoutError(PhotoBracketSorterError):
    default_user_message = "ExifTool не ответил за отведенное время."


class MetadataParseError(PhotoBracketSorterError):
    default_user_message = "Не удалось разобрать метаданные."


class UnsupportedPathError(PhotoBracketSorterError):
    default_user_message = "Путь не поддерживается."


class MTPPathError(PhotoBracketSorterError):
    default_user_message = "Виртуальные MTP-пути не поддерживаются."


class FormatDetectionError(PhotoBracketSorterError):
    default_user_message = "Не удалось определить формат файла."


class PreviewDecodeError(PhotoBracketSorterError):
    default_user_message = "Не удалось извлечь превью."


class AnalysisCancelled(PhotoBracketSorterError):
    default_user_message = "Анализ отменен."


class CopyCancelled(PhotoBracketSorterError):
    default_user_message = "Копирование отменено."


class MoveCancelled(PhotoBracketSorterError):
    default_user_message = "Перемещение отменено."


class CopyVerificationError(PhotoBracketSorterError):
    default_user_message = "Проверка копии не пройдена."


class InsufficientSpaceError(PhotoBracketSorterError):
    default_user_message = "Недостаточно свободного места."


class DestinationCollisionError(PhotoBracketSorterError):
    default_user_message = "Конфликт имен в папке результата."


class ReportWriteError(PhotoBracketSorterError):
    default_user_message = "Не удалось записать отчет."


class SettingsError(PhotoBracketSorterError):
    default_user_message = "Некорректные настройки."


class ManualSeriesError(PhotoBracketSorterError):
    default_user_message = "Не удалось создать ручную серию."


class StaleAnalysisError(PhotoBracketSorterError):
    default_user_message = "Файлы изменились после анализа. Повторите анализ."


class ProfileError(PhotoBracketSorterError):
    default_user_message = "Некорректный профиль камеры."


class SessionError(PhotoBracketSorterError):
    default_user_message = "Не удалось прочитать файл сессии."
