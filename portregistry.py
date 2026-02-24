# -*- coding: utf-8 -*-
"""
Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0

Weave: A modular PySide6 framework for the visual synthesis 
and execution of high-concurrency simulation workflows.
Copyright (c) 2026 opticsWolf

SPDX-License-Identifier: Apache-2.0
"""

import re
import json
from pathlib import Path
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import ClassVar, Dict, Optional, Type, Tuple, Union, Callable, Any
from PySide6.QtGui import QColor, QImage, QPixmap

# --- Type Definitions ---
ConverterFunc = Callable[[Any], Any]
ValidatorFunc = Callable[[Any], bool]


@dataclass(frozen=True)
class PortType:
    """
    Advanced Port Definition.
    Integrates Visuals (Color), Logic (Validators), and Casting (Inheritance).
    """
    name: str
    color: QColor
    type_id: int
    python_type: Optional[Type] = field(default=None, compare=False, hash=False)

    # Inheritance: ID of the parent type (e.g., Int inherits from Number)
    base_type_id: int = field(default=-1, compare=False, hash=False)

    # Factory for creating default values (safe for mutable types like lists)
    default_factory: Callable[[], Any] = field(default=lambda: None, compare=False, hash=False)

    # Logic to validate data before processing
    validator: ValidatorFunc = field(default=lambda x: True, compare=False, hash=False)

    # UI String formatter
    formatter: Callable[[Any], str] = field(default=str, compare=False, hash=False)

    def can_connect_from(self, other: 'PortType') -> bool:
        """
        Helper that asks the Registry: "Can 'other' connect to ME?"
        Used by NodeCanvas validation.
        """
        is_valid, _ = PortRegistry.get_converter(other, self)
        return is_valid

    def cast_value(self, value: Any, source_type: 'PortType') -> Any:
        """
        Runtime helper to convert a value from source_type to this type.
        """
        _, converter = PortRegistry.get_converter(source_type, self)
        if converter:
            return converter(value)
        return value


class PortRegistry:
    """
    Central Manager for PortTypes.
    """
    _by_name: ClassVar[Dict[str, PortType]] = {}
    _by_id: ClassVar[Dict[int, PortType]] = {}
    _cast_registry: ClassVar[Dict[Tuple[int, int], Optional[ConverterFunc]]] = {}
    _next_id: ClassVar[int] = 200  # Auto IDs start above built-in range (0-199)

    @classmethod
    def next_type_id(cls) -> int:
        """Return the next available type_id and increment the counter."""
        tid = cls._next_id
        cls._next_id += 1
        return tid

    @classmethod
    def register(cls,
                 name: str,
                 color: QColor,
                 type_id: Optional[int] = None,
                 python_type: Optional[Type] = None,
                 base_type_id: int = -1,
                 default: Any = None,
                 validator: Optional[ValidatorFunc] = None,
                 formatter: Optional[Callable[[Any], str]] = None,
                 casts_to: Optional[Dict[Union[int, str], ConverterFunc]] = None) -> PortType:

        lower_name = name.lower()

        # Auto-assign ID if not provided
        if type_id is None:
            type_id = cls.next_type_id()

        # Collision guards
        if type_id in cls._by_id:
            raise ValueError(f"type_id {type_id} already registered to '{cls._by_id[type_id].name}'")
        if lower_name in cls._by_name:
            raise ValueError(f"Port name '{name}' already registered")

        # Determine default factory
        fact = default if callable(default) else (lambda: default)

        new_type = PortType(
            name=name,
            color=color,
            type_id=type_id,
            python_type=python_type,
            base_type_id=base_type_id,
            default_factory=fact,
            validator=validator or (lambda x: True),
            formatter=formatter or str
        )

        cls._by_name[lower_name] = new_type
        cls._by_id[type_id] = new_type

        # Register explicit casts
        if casts_to:
            for target, converter in casts_to.items():
                target_id = cls._resolve_target_id(target)
                if target_id != -1:
                    cls._cast_registry[(type_id, target_id)] = converter

        return new_type

    @classmethod
    def get(cls, name_or_id: Union[str, int]) -> PortType:
        """Retrieve a PortType by name (string) or ID (int). Returns 'Generic' if not found."""
        if isinstance(name_or_id, int):
            return cls._by_id.get(name_or_id, cls._by_name.get('generic'))

        name = str(name_or_id).lower()
        return cls._by_name.get(name, cls._by_name.get('generic'))

    @classmethod
    def get_converter(cls, source: PortType, target: PortType) -> Tuple[bool, Optional[ConverterFunc]]:
        """
        O(1) Check for compatibility.
        Returns: (is_compatible, converter_function)
        """
        if source is None or target is None: return False, None

        # 1. Identity
        if source.type_id == target.type_id: return True, None

        # 2. Explicit Casts
        key = (source.type_id, target.type_id)
        if key in cls._cast_registry: return True, cls._cast_registry[key]

        # 3. Inheritance (Upcasting)
        if source.base_type_id == target.type_id: return True, None

        # 4. Generic Handling
        if target.name.lower() == "generic": return True, None

        return False, None

    @classmethod
    def _resolve_target_id(cls, target: Union[int, str]) -> int:
        if isinstance(target, int): return target
        if isinstance(target, str):
            t = cls._by_name.get(target.lower())
            return t.type_id if t else -1
        return -1


# ============================================================================
# BUILT-IN TYPE ID MAP
# ============================================================================
#
#  PRIMITIVES
#    0  = Generic           (accepts everything — the universal fallback)
#    1  = Dummy             (visual-only — minimised node summary ports)
#   10  = Number            (abstract numeric parent)
#   11  = Float
#   12  = Int
#   20  = Bool
#   30  = String
#
#  COLLECTIONS
#   40  = Collection        (abstract parent for ordered/unordered containers)
#   41  = List
#   42  = Tuple
#   43  = Set
#   50  = Dict
#   51  = JSON              (serialized interchange — wraps dict/list)
#
#  BINARY / RAW
#   60  = Bytes
#
#  FLOW CONTROL
#   70  = Exec              (execution trigger — carries no data)
#
#  ARRAYS / DATAFRAMES
#   80  = NdArray           (numpy — optional)
#   81  = DataFrame         (polars — optional)
#
#  IMAGE
#  100  = Image             (abstract parent for all image types)
#  101  = QImage            (PySide6 QImage)
#  102  = QPixmap           (PySide6 QPixmap)
#  103  = PILImage          (Pillow — optional)
#
#  UTILITIES
#  110  = DateTime
#  111  = Color             (QColor)
#  120  = Path              (pathlib.Path / filepath)
#  121  = Enum              (enum selection / choice)
#  122  = Regex             (re.Pattern)
#  123  = Error             (exception / result wrapper)
#
#  CUSTOM
#  200+ = Reserved for user-registered custom types
#
# ============================================================================


def setup_default_ports():
    """Register all built-in port types. Called once on import."""

    _reg = PortRegistry.register
    _cast = PortRegistry._cast_registry

    # ========================================================================
    # PRIMITIVES
    # ========================================================================

    GENERIC = _reg(
        "Generic", QColor(131, 134, 142), 0, object
    )

    # --- Dummy (visual-only port used by minimised node summary slots) ---
    DUMMY = _reg(
        "Dummy", QColor(124, 124, 124), 1, None,
        base_type_id=-1
    )

    # --- Numeric ---
    NUMBER = _reg(
        "Number", QColor(100, 100, 100), 10, object,
        base_type_id=0
    )

    FLOAT = _reg(
        "Float", QColor(0, 255, 100), 11, float,
        base_type_id=10,
        default=0.0,
        formatter=lambda x: f"{float(x):.2f}"
    )

    INT = _reg(
        "Int", QColor(0, 150, 255), 12, int,
        base_type_id=10,
        default=0,
        casts_to={11: float}  # Int -> Float
    )

    # --- Boolean ---
    BOOL = _reg(
        "Bool", QColor(255, 50, 50), 20, bool,
        default=False,
        casts_to={
            12: int,    # Bool -> Int  (True=1)
            11: float,  # Bool -> Float (True=1.0)
            30: str,    # Bool -> String ("True"/"False")
        }
    )

    # --- String ---
    STRING = _reg(
        "String", QColor(255, 165, 0), 30, str,
        default="",
        casts_to={
            41: lambda s: list(s),    # String -> List (chars)
            42: lambda s: tuple(s),   # String -> Tuple
            120: lambda s: Path(s),   # String -> Path
        }
    )

    # ========================================================================
    # COLLECTIONS
    # ========================================================================

    COLLECTION = _reg(
        "Collection", QColor(160, 120, 200), 40, object,
        base_type_id=0,
        formatter=lambda x: f"Collection({len(x)} items)" if hasattr(x, '__len__') else str(x)
    )

    LIST = _reg(
        "List", QColor(180, 100, 255), 41, list,
        base_type_id=40,
        default=list,
        formatter=lambda x: f"List[{len(x)}]",
        casts_to={
            42: tuple,                # List -> Tuple
            43: set,                  # List -> Set
            30: lambda x: str(x),    # List -> String
            51: lambda x: json.dumps(x, default=str),  # List -> JSON
        }
    )

    TUPLE = _reg(
        "Tuple", QColor(140, 80, 220), 42, tuple,
        base_type_id=40,
        default=tuple,
        formatter=lambda x: f"Tuple({len(x)})",
        casts_to={
            41: list,                 # Tuple -> List
            43: set,                  # Tuple -> Set
            30: lambda x: str(x),    # Tuple -> String
        }
    )

    SET = _reg(
        "Set", QColor(100, 60, 180), 43, set,
        base_type_id=40,
        default=set,
        formatter=lambda x: f"Set{{{len(x)}}}",
        casts_to={
            41: lambda x: list(x),   # Set -> List
            42: lambda x: tuple(x),  # Set -> Tuple
            30: lambda x: str(x),    # Set -> String
        }
    )

    # --- Dict ---
    DICT = _reg(
        "Dict", QColor(50, 200, 200), 50, dict,
        default=dict,
        formatter=lambda x: f"Dict{{{len(x)} keys}}",
        casts_to={
            41: lambda d: list(d.items()),    # Dict -> List of (k,v)
            42: lambda d: tuple(d.items()),   # Dict -> Tuple of (k,v)
            43: lambda d: set(d.keys()),      # Dict -> Set of keys
            30: lambda d: str(d),             # Dict -> String
            51: lambda d: json.dumps(d, default=str),  # Dict -> JSON
        }
    )

    # --- JSON (serialized interchange string — wraps dict/list) ---
    JSON = _reg(
        "JSON", QColor(60, 220, 180), 51, str,
        default=lambda: "{}",
        validator=lambda x: _is_valid_json(x),
        formatter=lambda x: f"JSON({len(x)} chars)" if isinstance(x, str) else str(x),
        casts_to={
            50: lambda j: json.loads(j),      # JSON -> Dict
            41: lambda j: json.loads(j) if isinstance(json.loads(j), list) else list(json.loads(j).items()),  # JSON -> List
            30: lambda j: j,                   # JSON -> String (identity — it's already a string)
        }
    )

    # ========================================================================
    # BINARY / RAW
    # ========================================================================

    BYTES = _reg(
        "Bytes", QColor(80, 80, 200), 60, bytes,
        default=bytes,
        formatter=lambda x: f"Bytes({len(x)})",
        casts_to={
            30: lambda b: b.decode('utf-8', errors='replace'),  # Bytes -> String
            41: lambda b: list(b),                                # Bytes -> List[int]
        }
    )

    # ========================================================================
    # FLOW CONTROL
    # ========================================================================

    EXEC = _reg(
        "Exec", QColor(255, 255, 255), 70, type(None),
        default=None,
        formatter=lambda x: "▶",
    )

    # ========================================================================
    # ARRAYS / DATAFRAMES (optional — guarded imports)
    # ========================================================================

    # --- Numpy NdArray ---
    try:
        import numpy as np
        NDARRAY = _reg(
            "NdArray", QColor(0, 200, 180), 80, np.ndarray,
            default=lambda: np.empty(0),
            formatter=lambda x: f"NdArray{x.shape} {x.dtype}",
            casts_to={
                41: lambda x: x.tolist(),                                          # NdArray -> List
                42: lambda x: tuple(x.tolist()),                                   # NdArray -> Tuple
                11: lambda x: float(x) if x.size == 1 else float(x.flat[0]),      # NdArray -> Float
                12: lambda x: int(x) if x.size == 1 else int(x.flat[0]),          # NdArray -> Int
                30: lambda x: str(x),                                              # NdArray -> String
            }
        )
        # Reverse casts into NdArray
        _cast[(41, 80)] = lambda x: np.array(x)    # List  -> NdArray
        _cast[(42, 80)] = lambda x: np.array(x)    # Tuple -> NdArray
    except ImportError:
        pass

    # --- Polars DataFrame ---
    try:
        import polars as pl
        DATAFRAME = _reg(
            "DataFrame", QColor(220, 130, 50), 81, pl.DataFrame,
            default=lambda: pl.DataFrame(),
            formatter=lambda x: f"DataFrame({x.shape[0]}×{x.shape[1]})",
            casts_to={
                50: lambda df: df.to_dict(),                                       # DataFrame -> Dict
                41: lambda df: df.to_dicts(),                                      # DataFrame -> List[dict] (rows)
                51: lambda df: df.write_json(),                                    # DataFrame -> JSON
                30: lambda df: str(df),                                            # DataFrame -> String
            }
        )
        # Reverse casts into DataFrame
        _cast[(50, 81)] = lambda d: pl.DataFrame(d)                                # Dict -> DataFrame
        _cast[(41, 81)] = lambda lst: pl.DataFrame(lst) if lst else pl.DataFrame() # List[dict] -> DataFrame
        _cast[(51, 81)] = lambda j: pl.read_json(j.encode())                       # JSON -> DataFrame

        # NdArray <-> DataFrame (only if numpy is also available)
        if 80 in PortRegistry._by_id:
            import numpy as np
            _cast[(80, 81)] = lambda arr: pl.DataFrame({f"col_{i}": arr[:, i] for i in range(arr.shape[1])} if arr.ndim == 2 else {"col_0": arr})  # NdArray -> DataFrame
            _cast[(81, 80)] = lambda df: df.to_numpy()                             # DataFrame -> NdArray
    except ImportError:
        pass

    # ========================================================================
    # IMAGE TYPES
    # ========================================================================

    # --- Abstract Image Parent ---
    IMAGE = _reg(
        "Image", QColor(200, 50, 200), 100, object,
        base_type_id=0,
        formatter=lambda x: "Image"
    )

    # --- QImage (PySide6) ---
    QIMAGE = _reg(
        "QImage", QColor(210, 70, 220), 101, QImage,
        base_type_id=100,
        default=QImage,
        formatter=lambda x: f"QImage({x.width()}×{x.height()})" if isinstance(x, QImage) else str(x),
        casts_to={
            102: lambda img: QPixmap.fromImage(img),   # QImage -> QPixmap
            60: lambda img: _qimage_to_bytes(img),     # QImage -> Bytes
        }
    )

    # --- QPixmap (PySide6) ---
    QPIXMAP = _reg(
        "QPixmap", QColor(220, 90, 240), 102, QPixmap,
        base_type_id=100,
        default=QPixmap,
        formatter=lambda x: f"QPixmap({x.width()}×{x.height()})" if isinstance(x, QPixmap) else str(x),
        casts_to={
            101: lambda px: px.toImage(),              # QPixmap -> QImage
            60: lambda px: _qimage_to_bytes(px.toImage()),  # QPixmap -> Bytes
        }
    )

    # --- PIL Image (optional) ---
    try:
        from PIL import Image as PILImageLib
        PILIMAGE = _reg(
            "PILImage", QColor(190, 40, 180), 103, PILImageLib.Image,
            base_type_id=100,
            default=lambda: PILImageLib.new('RGBA', (1, 1)),
            formatter=lambda x: f"PILImage({x.width}×{x.height} {x.mode})" if hasattr(x, 'mode') else str(x),
            casts_to={
                30: lambda img: f"PILImage({img.width}×{img.height} {img.mode})",  # PIL -> String
            }
        )

        # PIL <-> QImage cross-casts
        _cast[(103, 101)] = lambda img: _pil_to_qimage(img)     # PIL -> QImage
        _cast[(101, 103)] = lambda img: _qimage_to_pil(img)     # QImage -> PIL

        # PIL <-> NdArray (if numpy available)
        if 80 in PortRegistry._by_id:
            import numpy as np
            _cast[(103, 80)] = lambda img: np.array(img)          # PIL -> NdArray
            _cast[(80, 103)] = lambda arr: PILImageLib.fromarray(arr.astype('uint8'))  # NdArray -> PIL
    except ImportError:
        pass

    # ========================================================================
    # UTILITIES
    # ========================================================================

    # --- DateTime ---
    DATETIME = _reg(
        "DateTime", QColor(255, 200, 80), 110, datetime,
        default=datetime.now,
        formatter=lambda x: x.strftime("%Y-%m-%d %H:%M:%S") if isinstance(x, datetime) else str(x),
        casts_to={
            30: lambda dt: dt.isoformat(),                # DateTime -> String (ISO 8601)
            11: lambda dt: dt.timestamp(),                # DateTime -> Float (unix timestamp)
            12: lambda dt: int(dt.timestamp()),           # DateTime -> Int (unix timestamp)
        }
    )
    # Reverse: String -> DateTime
    _cast[(30, 110)] = lambda s: datetime.fromisoformat(s)

    # --- Color (QColor) ---
    COLOR = _reg(
        "Color", QColor(255, 100, 200), 111, QColor,
        default=lambda: QColor(255, 255, 255),
        formatter=lambda c: c.name() if isinstance(c, QColor) else str(c),
        casts_to={
            30: lambda c: c.name(),                                # Color -> String ("#rrggbb")
            41: lambda c: [c.red(), c.green(), c.blue(), c.alpha()],  # Color -> List [R,G,B,A]
            42: lambda c: (c.red(), c.green(), c.blue(), c.alpha()),  # Color -> Tuple (R,G,B,A)
            50: lambda c: {"r": c.red(), "g": c.green(), "b": c.blue(), "a": c.alpha()},  # Color -> Dict
        }
    )
    # Reverse casts into Color
    _cast[(30, 111)] = lambda s: QColor(s)                          # String ("#ff0000") -> Color
    _cast[(41, 111)] = lambda lst: QColor(*lst[:4])                 # List [R,G,B,A] -> Color
    _cast[(42, 111)] = lambda t: QColor(*t[:4])                     # Tuple (R,G,B,A) -> Color

    # --- Path / FilePath ---
    FILEPATH = _reg(
        "Path", QColor(180, 160, 100), 120, Path,
        default=lambda: Path("."),
        formatter=lambda p: str(p),
        casts_to={
            30: lambda p: str(p),                         # Path -> String
            60: lambda p: p.read_bytes() if p.is_file() else b"",  # Path -> Bytes (read file)
        }
    )
    # Reverse: String -> Path
    _cast[(30, 120)] = lambda s: Path(s)

    # --- Enum / Choice ---
    ENUM = _reg(
        "Enum", QColor(200, 200, 50), 121, Enum,
        default=None,
        formatter=lambda e: f"{e.name}={e.value}" if isinstance(e, Enum) else str(e),
        casts_to={
            30: lambda e: e.name if isinstance(e, Enum) else str(e),    # Enum -> String (name)
            12: lambda e: e.value if isinstance(e, Enum) and isinstance(e.value, int) else 0,  # Enum -> Int (value)
        }
    )

    # --- Regex (compiled pattern) ---
    REGEX = _reg(
        "Regex", QColor(255, 120, 120), 122, re.Pattern,
        default=lambda: re.compile(""),
        formatter=lambda r: f"re({r.pattern!r})" if isinstance(r, re.Pattern) else str(r),
        casts_to={
            30: lambda r: r.pattern,                      # Regex -> String (pattern text)
        }
    )
    # Reverse: String -> Regex
    _cast[(30, 122)] = lambda s: re.compile(s)

    # --- Error / Result ---
    ERROR = _reg(
        "Error", QColor(255, 0, 0), 123, Exception,
        default=None,
        formatter=lambda e: f"Error({type(e).__name__}: {e})" if isinstance(e, Exception) else str(e),
        casts_to={
            30: lambda e: f"{type(e).__name__}: {e}",     # Error -> String
            20: lambda e: False,                           # Error -> Bool (always falsy)
        }
    )


# ============================================================================
# INTERNAL HELPERS (used by cast functions above)
# ============================================================================

def _is_valid_json(x: Any) -> bool:
    """Validator for JSON port type."""
    if not isinstance(x, str):
        return False
    try:
        json.loads(x)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def _qimage_to_bytes(img: QImage) -> bytes:
    """Convert QImage to raw PNG bytes."""
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _pil_to_qimage(pil_img) -> QImage:
    """Convert PIL Image to QImage."""
    pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    return QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888).copy()


def _qimage_to_pil(qimg: QImage):
    """Convert QImage to PIL Image."""
    from PIL import Image as PILImageLib
    qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = qimg.width(), qimg.height()
    ptr = qimg.bits()
    return PILImageLib.frombytes("RGBA", (width, height), bytes(ptr))


# Run setup immediately on import so types exist
setup_default_ports()