"""Offline public RPC constants and enum surface for notebooklm-py==0.7.2."""

from __future__ import annotations

from enum import Enum, IntEnum

from ..config import DEFAULT_BASE_URL
from .overrides import resolve_rpc_id as resolve_rpc_id

BASE_URL = DEFAULT_BASE_URL
BATCHEXECUTE_URL = DEFAULT_BASE_URL + "/_/LabsTailwindUi/data/batchexecute"
BATCH_EXECUTE_URL = BATCHEXECUTE_URL
_QUERY_ENDPOINT_PATH = (
    "/_/LabsTailwindUi/data/google{dot}internal.labs.tailwind.orchestration.v1."
    "LabsTailwindOrchestrationService/GenerateFreeFormStreamed"
).format(dot=".")
QUERY_URL = DEFAULT_BASE_URL + _QUERY_ENDPOINT_PATH
UPLOAD_URL = DEFAULT_BASE_URL + "/upload/_/"
FLASHCARDS_VARIANT = 1
QUIZ_VARIANT = 2
INTERACTIVE_MIND_MAP_VARIANT = 4


class ArtifactStatus(IntEnum):
    """Pinned notebooklm-py==0.7.2 ArtifactStatus values."""

    COMPLETED = 3
    FAILED = 4
    PENDING = 2
    PROCESSING = 1


class ArtifactType(str, Enum):
    """Pinned notebooklm-py==0.7.2 ArtifactType values."""

    AUDIO = "audio"
    DATA_TABLE = "data_table"
    FLASHCARDS = "flashcards"
    INFOGRAPHIC = "infographic"
    MIND_MAP = "mind_map"
    QUIZ = "quiz"
    REPORT = "report"
    SLIDE_DECK = "slide_deck"
    UNKNOWN = "unknown"
    VIDEO = "video"

    def __call__(self) -> "ArtifactType":
        return self


class ArtifactTypeCode(IntEnum):
    """Pinned notebooklm-py==0.7.2 ArtifactTypeCode values."""

    AUDIO = 1
    DATA_TABLE = 9
    INFOGRAPHIC = 7
    MIND_MAP = 5
    QUIZ = 4
    QUIZ_FLASHCARD = 4
    REPORT = 2
    SLIDE_DECK = 8
    VIDEO = 3


class AudioFormat(IntEnum):
    """Pinned notebooklm-py==0.7.2 AudioFormat values."""

    BRIEF = 2
    CRITIQUE = 3
    DEBATE = 4
    DEEP_DIVE = 1


class AudioLength(IntEnum):
    """Pinned notebooklm-py==0.7.2 AudioLength values."""

    DEFAULT = 2
    LONG = 3
    SHORT = 1


class ChatGoal(IntEnum):
    """Pinned notebooklm-py==0.7.2 ChatGoal values."""

    CUSTOM = 2
    DEFAULT = 1
    LEARNING_GUIDE = 3


class ChatMode(str, Enum):
    """Pinned notebooklm-py==0.7.2 ChatMode values."""

    CONCISE = "concise"
    DEFAULT = "default"
    DETAILED = "detailed"
    LEARNING_GUIDE = "learning_guide"


class ChatResponseLength(IntEnum):
    """Pinned notebooklm-py==0.7.2 ChatResponseLength values."""

    DEFAULT = 1
    LONGER = 4
    SHORTER = 5


class DriveMimeType(str, Enum):
    """Pinned notebooklm-py==0.7.2 DriveMimeType values."""

    GOOGLE_DOC = "application/vnd.google-apps.document"
    GOOGLE_SHEETS = "application/vnd.google-apps.spreadsheet"
    GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
    PDF = "application/pdf"


class ExportType(IntEnum):
    """Pinned notebooklm-py==0.7.2 ExportType values."""

    DOCS = 1
    SHEETS = 2


class InfographicDetail(IntEnum):
    """Pinned notebooklm-py==0.7.2 InfographicDetail values."""

    CONCISE = 1
    DETAILED = 3
    STANDARD = 2


class InfographicOrientation(IntEnum):
    """Pinned notebooklm-py==0.7.2 InfographicOrientation values."""

    LANDSCAPE = 1
    PORTRAIT = 2
    SQUARE = 3


class InfographicStyle(IntEnum):
    """Pinned notebooklm-py==0.7.2 InfographicStyle values."""

    ANIME = 9
    AUTO_SELECT = 1
    BENTO_GRID = 4
    BRICKS = 7
    CLAY = 8
    EDITORIAL = 5
    INSTRUCTIONAL = 6
    KAWAII = 10
    PROFESSIONAL = 3
    SCIENTIFIC = 11
    SKETCH_NOTE = 2


class MindMapKind(str, Enum):
    """Pinned notebooklm-py==0.7.2 MindMapKind values."""

    INTERACTIVE = "interactive"
    NOTE_BACKED = "note_backed"


class QuizDifficulty(IntEnum):
    """Pinned notebooklm-py==0.7.2 QuizDifficulty values."""

    EASY = 1
    HARD = 3
    MEDIUM = 2


class QuizQuantity(IntEnum):
    """Pinned notebooklm-py==0.7.2 QuizQuantity values."""

    FEWER = 1
    STANDARD = 2
    MORE = 2


class RPCErrorCode(IntEnum):
    """Pinned notebooklm-py==0.7.2 RPCErrorCode values."""

    FORBIDDEN = 403
    INVALID_REQUEST = 400
    NOT_FOUND = 404
    RATE_LIMITED = 429
    SERVER_ERROR = 500
    UNAUTHORIZED = 401
    UNKNOWN = 0


class RPCMethod(str, Enum):
    """Pinned notebooklm-py==0.7.2 RPCMethod values."""

    ADD_SOURCE = "izAoDd"
    ADD_SOURCE_FILE = "o4cbdc"
    CHECK_SOURCE_FRESHNESS = "yR9Yof"
    CREATE_ARTIFACT = "R7cb6c"
    CREATE_NOTE = "CYK0Xb"
    CREATE_NOTEBOOK = "CCqFvf"
    DELETE_ARTIFACT = "V5N4be"
    DELETE_CONVERSATION = "J7Gthc"
    DELETE_NOTE = "AH0mwd"
    DELETE_NOTEBOOK = "WWINqb"
    DELETE_SOURCE = "tGMBJ"
    EXPORT_ARTIFACT = "Krh3pd"
    GENERATE_MIND_MAP = "yyryJe"
    GET_CONVERSATION_TURNS = "khqZz"
    GET_INTERACTIVE_HTML = "v9rmvd"
    GET_LAST_CONVERSATION_ID = "hPTbtc"
    GET_NOTEBOOK = "rLM1Ne"
    GET_NOTES_AND_MIND_MAPS = "cFji9"
    GET_SHARE_STATUS = "JFMDGd"
    GET_SOURCE = "hizoJc"
    GET_SOURCE_GUIDE = "tr032e"
    GET_SUGGESTED_REPORTS = "ciyUvf"
    GET_USER_SETTINGS = "ZwVcOc"
    GET_USER_TIER = "ozz5Z"
    IMPORT_RESEARCH = "LBwxtb"
    LIST_ARTIFACTS = "gArtLc"
    LIST_NOTEBOOKS = "wXbhsf"
    POLL_RESEARCH = "e3bVqc"
    REFRESH_SOURCE = "FLmJqe"
    REMOVE_RECENTLY_VIEWED = "fejl7e"
    RENAME_ARTIFACT = "rc3d8d"
    RENAME_NOTEBOOK = "s0tc2d"
    RETRY_ARTIFACT = "Rytqqe"
    REVISE_SLIDE = "KmcKPe"
    SET_USER_SETTINGS = "hT54vc"
    SHARE_ARTIFACT = "RGP97b"
    SHARE_NOTEBOOK = "QDyure"
    START_DEEP_RESEARCH = "QA9ei"
    START_FAST_RESEARCH = "Ljjv0c"
    SUMMARIZE = "VfAZjd"
    UPDATE_NOTE = "cYAfTb"
    UPDATE_SOURCE = "b7Wfje"


class ReportFormat(str, Enum):
    """Pinned notebooklm-py==0.7.2 ReportFormat values."""

    BLOG_POST = "blog_post"
    BRIEFING_DOC = "briefing_doc"
    CUSTOM = "custom"
    STUDY_GUIDE = "study_guide"


class ResearchStatus(str, Enum):
    """Pinned notebooklm-py==0.7.2 ResearchStatus values."""

    COMPLETED = "completed"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    NOT_FOUND = "not_found"
    NO_RESEARCH = "no_research"


class ShareAccess(IntEnum):
    """Pinned notebooklm-py==0.7.2 ShareAccess values."""

    ANYONE_WITH_LINK = 1
    RESTRICTED = 0


class SharePermission(IntEnum):
    """Pinned notebooklm-py==0.7.2 SharePermission values."""

    EDITOR = 2
    OWNER = 1
    VIEWER = 3
    _REMOVE = 4


class ShareViewLevel(IntEnum):
    """Pinned notebooklm-py==0.7.2 ShareViewLevel values."""

    CHAT_ONLY = 1
    FULL_NOTEBOOK = 0


class SlideDeckFormat(IntEnum):
    """Pinned notebooklm-py==0.7.2 SlideDeckFormat values."""

    DETAILED_DECK = 1
    PRESENTER_SLIDES = 2


class SlideDeckLength(IntEnum):
    """Pinned notebooklm-py==0.7.2 SlideDeckLength values."""

    DEFAULT = 1
    SHORT = 2


class SourceStatus(IntEnum):
    """Pinned notebooklm-py==0.7.2 SourceStatus values."""

    ERROR = 3
    PREPARING = 5
    PROCESSING = 1
    READY = 2


class SourceType(str, Enum):
    """Pinned notebooklm-py==0.7.2 SourceType values."""

    CSV = "csv"
    DOCX = "docx"
    EPUB = "epub"
    GOOGLE_DOCS = "google_docs"
    GOOGLE_DRIVE_AUDIO = "google_drive_audio"
    GOOGLE_DRIVE_VIDEO = "google_drive_video"
    GOOGLE_SLIDES = "google_slides"
    GOOGLE_SPREADSHEET = "google_spreadsheet"
    IMAGE = "image"
    MARKDOWN = "markdown"
    MEDIA = "media"
    PASTED_TEXT = "pasted_text"
    PDF = "pdf"
    UNKNOWN = "unknown"
    WEB_PAGE = "web_page"
    YOUTUBE = "youtube"

    def __call__(self) -> "SourceType":
        return self


class VideoFormat(IntEnum):
    """Pinned notebooklm-py==0.7.2 VideoFormat values."""

    BRIEF = 2
    CINEMATIC = 3
    EXPLAINER = 1


class VideoStyle(IntEnum):
    """Pinned notebooklm-py==0.7.2 VideoStyle values."""

    ANIME = 7
    AUTO_SELECT = 1
    CLASSIC = 2
    CUSTOM = 0
    HERITAGE = 4
    KAWAII = 9
    PAPER_CRAFT = 5
    RETRO_PRINT = 8
    WATERCOLOR = 6
    WHITEBOARD = 3


def get_base_url() -> str:
    from ..config import get_base_url as _get_base_url

    return _get_base_url()


def get_batchexecute_url() -> str:
    return get_base_url() + "/_/LabsTailwindUi/data/batchexecute"


def get_query_url() -> str:
    return get_base_url() + _QUERY_ENDPOINT_PATH


def get_upload_url() -> str:
    return get_base_url() + "/upload/_/"


def artifact_status_to_str(status_code: int) -> str:
    mapping = {
        ArtifactStatus.PROCESSING: "in_progress",
        ArtifactStatus.PENDING: "pending",
        ArtifactStatus.COMPLETED: "completed",
        ArtifactStatus.FAILED: "failed",
    }
    return mapping.get(status_code, "unknown")


def source_status_to_str(status_code: int | SourceStatus) -> str:
    try:
        return SourceStatus(status_code).name.lower()
    except ValueError:
        return "unknown"
