# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave: Unified UUID Mixin
Provides consistent UUID handling across Nodes, Ports, and other entities.
"""

import uuid
from typing import Union, Optional


class UUIDMixin:
    """
    Mixin providing standardized UUID support.
    
    Guarantees:
    - _uuid: uuid.UUID object (private storage)
    - unique_id: str property (public, JSON-serializable)
    - get_uuid(): Returns uuid.UUID object
    - get_uuid_string(): Returns str
    """
    
    def _init_uuid(self, existing_uuid: Optional[Union[str, uuid.UUID]] = None):
        """
        Initialize UUID. Call this from the host class __init__.
        
        Args:
            existing_uuid: Optional existing UUID (string or object) to restore
        """
        if existing_uuid is not None:
            if isinstance(existing_uuid, uuid.UUID):
                self._uuid = existing_uuid
            else:
                try:
                    self._uuid = uuid.UUID(str(existing_uuid))
                except ValueError:
                    self._uuid = uuid.uuid4()
        else:
            self._uuid = uuid.uuid4()
    
    @property
    def unique_id(self) -> str:
        """JSON-serializable string UUID (primary external identifier)."""
        return str(self._uuid)
    
    @unique_id.setter
    def unique_id(self, value: Union[str, uuid.UUID]):
        """Allow setting UUID during deserialization."""
        if isinstance(value, uuid.UUID):
            self._uuid = value
        else:
            self._uuid = uuid.UUID(str(value))
    
    def get_uuid(self) -> uuid.UUID:
        """Get the UUID object."""
        return self._uuid
    
    def get_uuid_string(self) -> str:
        """Get UUID as string (convenience method)."""
        return str(self._uuid)
    
    def matches_uuid(self, other: Union[str, uuid.UUID, 'UUIDMixin']) -> bool:
        """Check if this entity matches the given UUID."""
        if isinstance(other, UUIDMixin):
            return self._uuid == other._uuid
        if isinstance(other, uuid.UUID):
            return self._uuid == other
        try:
            return self._uuid == uuid.UUID(str(other))
        except ValueError:
            return False