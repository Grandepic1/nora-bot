from dataclasses import dataclass


@dataclass(slots=True)
class MediaAttachment:
    data: bytes
    mime_type: str
    filename: str | None
        
