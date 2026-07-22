"""ZeroNotebookLM stdlib-only compatibility scaffold."""

from __future__ import annotations

__version__ = "0.7.2"
__project__ = "zero-notebooklm"
__upstream_requirement__ = "notebooklm-py==0.7.2"

from .types import AccountLimits as AccountLimits
from .types import AccountTier as AccountTier
from .types import Artifact as Artifact
from .exceptions import ArtifactDownloadError as ArtifactDownloadError
from .exceptions import ArtifactError as ArtifactError
from .exceptions import (
    ArtifactFeatureUnavailableError as ArtifactFeatureUnavailableError,
)
from .exceptions import ArtifactInProgressTimeoutError as ArtifactInProgressTimeoutError
from .exceptions import ArtifactNotFoundError as ArtifactNotFoundError
from .exceptions import ArtifactNotReadyError as ArtifactNotReadyError
from .exceptions import ArtifactParseError as ArtifactParseError
from .exceptions import ArtifactPendingTimeoutError as ArtifactPendingTimeoutError
from .exceptions import ArtifactTimeoutError as ArtifactTimeoutError
from .types import ArtifactType as ArtifactType
from .types import AskResult as AskResult
from .rpc.types import AudioFormat as AudioFormat
from .rpc.types import AudioLength as AudioLength
from .exceptions import AuthError as AuthError
from .exceptions import AuthExtractionError as AuthExtractionError
from .auth import AuthTokens as AuthTokens
from .exceptions import ChatError as ChatError
from .rpc.types import ChatGoal as ChatGoal
from .rpc.types import ChatMode as ChatMode
from .types import ChatReference as ChatReference
from .rpc.types import ChatResponseLength as ChatResponseLength
from .exceptions import ChatResponseParseError as ChatResponseParseError
from .types import CitedSourceSelection as CitedSourceSelection
from .exceptions import ClientError as ClientError
from .types import ClientMetricsSnapshot as ClientMetricsSnapshot
from .exceptions import ConfigurationError as ConfigurationError
from .types import ConnectionLimits as ConnectionLimits
from .types import ConversationTurn as ConversationTurn
from .exceptions import DecodingError as DecodingError
from .rpc.types import DriveMimeType as DriveMimeType
from .rpc.types import ExportType as ExportType
from .types import GenerationStatus as GenerationStatus
from .rpc.types import InfographicDetail as InfographicDetail
from .rpc.types import InfographicOrientation as InfographicOrientation
from .rpc.types import InfographicStyle as InfographicStyle
from .types import MindMap as MindMap
from .exceptions import MindMapError as MindMapError
from .rpc.types import MindMapKind as MindMapKind
from .exceptions import MindMapNotFoundError as MindMapNotFoundError
from .types import MindMapResult as MindMapResult
from .exceptions import NetworkError as NetworkError
from .exceptions import NonIdempotentRetryError as NonIdempotentRetryError
from .exceptions import NotFoundError as NotFoundError
from .types import Note as Note
from .exceptions import NoteError as NoteError
from .exceptions import NoteNotFoundError as NoteNotFoundError
from .types import Notebook as Notebook
from .types import NotebookDescription as NotebookDescription
from .exceptions import NotebookError as NotebookError
from .client import NotebookLMClient as NotebookLMClient
from .exceptions import NotebookLMError as NotebookLMError
from .exceptions import NotebookLimitError as NotebookLimitError
from .types import NotebookMetadata as NotebookMetadata
from .exceptions import NotebookNotFoundError as NotebookNotFoundError
from .rpc.types import QuizDifficulty as QuizDifficulty
from .rpc.types import QuizQuantity as QuizQuantity
from .exceptions import RPCError as RPCError
from .exceptions import RPCResponseTooLargeError as RPCResponseTooLargeError
from .exceptions import RPCTimeoutError as RPCTimeoutError
from .exceptions import RateLimitError as RateLimitError
from .rpc.types import ReportFormat as ReportFormat
from .types import ReportSuggestion as ReportSuggestion
from .exceptions import ResearchError as ResearchError
from .types import ResearchSource as ResearchSource
from .types import ResearchStart as ResearchStart
from .rpc.types import ResearchStatus as ResearchStatus
from .types import ResearchTask as ResearchTask
from .exceptions import ResearchTaskMismatchError as ResearchTaskMismatchError
from .exceptions import ResearchTimeoutError as ResearchTimeoutError
from .types import RpcTelemetryEvent as RpcTelemetryEvent
from .exceptions import ServerError as ServerError
from .rpc.types import ShareAccess as ShareAccess
from .rpc.types import SharePermission as SharePermission
from .types import ShareStatus as ShareStatus
from .rpc.types import ShareViewLevel as ShareViewLevel
from .types import SharedUser as SharedUser
from .rpc.types import SlideDeckFormat as SlideDeckFormat
from .rpc.types import SlideDeckLength as SlideDeckLength
from .types import Source as Source
from .exceptions import SourceAddError as SourceAddError
from .exceptions import SourceError as SourceError
from .types import SourceFulltext as SourceFulltext
from .types import SourceGuide as SourceGuide
from .exceptions import SourceNotFoundError as SourceNotFoundError
from .exceptions import SourceProcessingError as SourceProcessingError
from .types import SourceStatus as SourceStatus
from .types import SourceSummary as SourceSummary
from .exceptions import SourceTimeoutError as SourceTimeoutError
from .types import SourceType as SourceType
from .types import SuggestedTopic as SuggestedTopic
from .exceptions import UnknownRPCMethodError as UnknownRPCMethodError
from .types import UnknownTypeWarning as UnknownTypeWarning
from .exceptions import ValidationError as ValidationError
from .rpc.types import VideoFormat as VideoFormat
from .rpc.types import VideoStyle as VideoStyle
from .exceptions import WaitTimeoutError as WaitTimeoutError
from ._logging import configure_logging as configure_logging
from ._logging import correlation_id as correlation_id
from ._logging import get_request_id as get_request_id
from ._logging import reset_request_id as reset_request_id
from .utils import resolve_chat_reference_passage as resolve_chat_reference_passage
from ._logging import set_request_id as set_request_id

configure_logging()

__all__ = [
    '__version__',
    'NotebookLMClient',
    'AuthTokens',
    'correlation_id',
    'get_request_id',
    'set_request_id',
    'reset_request_id',
    'AccountLimits',
    'AccountTier',
    'ConnectionLimits',
    'ClientMetricsSnapshot',
    'RpcTelemetryEvent',
    'Notebook',
    'NotebookDescription',
    'NotebookMetadata',
    'SuggestedTopic',
    'Source',
    'SourceFulltext',
    'SourceGuide',
    'SourceSummary',
    'Artifact',
    'GenerationStatus',
    'ReportSuggestion',
    'MindMap',
    'MindMapKind',
    'MindMapResult',
    'Note',
    'ConversationTurn',
    'ChatReference',
    'AskResult',
    'ChatMode',
    'CitedSourceSelection',
    'ResearchStatus',
    'ResearchSource',
    'ResearchTask',
    'ResearchStart',
    'SharedUser',
    'ShareStatus',
    'resolve_chat_reference_passage',
    'NotebookLMError',
    'ValidationError',
    'ConfigurationError',
    'NotFoundError',
    'RPCError',
    'DecodingError',
    'UnknownRPCMethodError',
    'AuthError',
    'AuthExtractionError',
    'NetworkError',
    'RPCTimeoutError',
    'RPCResponseTooLargeError',
    'RateLimitError',
    'ServerError',
    'ClientError',
    'NonIdempotentRetryError',
    'NotebookError',
    'NotebookNotFoundError',
    'NotebookLimitError',
    'ChatError',
    'ChatResponseParseError',
    'SourceError',
    'SourceAddError',
    'SourceProcessingError',
    'SourceTimeoutError',
    'SourceNotFoundError',
    'ArtifactError',
    'ArtifactFeatureUnavailableError',
    'ArtifactNotFoundError',
    'ArtifactNotReadyError',
    'ArtifactParseError',
    'ArtifactDownloadError',
    'ArtifactTimeoutError',
    'ArtifactPendingTimeoutError',
    'ArtifactInProgressTimeoutError',
    'ResearchError',
    'ResearchTimeoutError',
    'ResearchTaskMismatchError',
    'NoteError',
    'NoteNotFoundError',
    'MindMapError',
    'MindMapNotFoundError',
    'WaitTimeoutError',
    'UnknownTypeWarning',
    'SourceType',
    'ArtifactType',
    'AudioFormat',
    'AudioLength',
    'VideoFormat',
    'VideoStyle',
    'QuizQuantity',
    'QuizDifficulty',
    'InfographicOrientation',
    'InfographicDetail',
    'InfographicStyle',
    'SlideDeckFormat',
    'SlideDeckLength',
    'ReportFormat',
    'ChatGoal',
    'ChatResponseLength',
    'DriveMimeType',
    'ExportType',
    'SourceStatus',
    'ShareAccess',
    'ShareViewLevel',
    'SharePermission',
]
