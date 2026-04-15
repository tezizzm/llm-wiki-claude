from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class NamingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["preserve_path", "basename"] = "preserve_path"
    prefix: str = "source"


class SyncSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    root: str
    include: List[str] = Field(default_factory=list)
    exclude: List[str] = Field(default_factory=list)
    naming: NamingConfig = Field(default_factory=NamingConfig)


class SyncConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    sources: List[SyncSourceConfig]


class LowSignalSourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opaque_task_regex: str
    name_patterns: List[str]


class TopicSettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_chars: int
    max_words: int
    blocked_suffixes: List[str]
    blocked_prefix_patterns: List[str]


class EntitySettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_chars: int
    max_words: int
    allowlist: List[str]
    blocked_suffixes: List[str]
    blocked_identifier_suffixes: List[str]
    blocked_prefix_patterns: List[str]
    blocked_word_fragments: List[str]
    blocked_single_word_prefixes: List[str]


class IngestSettingsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    max_source_chars: int
    max_topics: int
    max_entities: int
    low_signal_sources: LowSignalSourcesConfig
    topics: TopicSettingsConfig
    entities: EntitySettingsConfig
