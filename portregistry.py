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
    Integrates Visuals (Color Index), Logic (Validators), and Casting (Inheritance).

    The ``color_index`` field is a zero-based index into the active theme's
    ``trace_color_palette`` (managed by the StyleManager under
    ``StyleCategory.TRACE``).  Call ``PortRegistry.resolve_color(color_index)``
    to obtain the concrete ``QColor`` for the current theme.
    """
    name: str
    color_index: int
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

    Colour resolution
    -----------------
    Port types store a ``color_index`` that points into the active theme's
    ``trace_color_palette``.  Use ``resolve_color(index)`` to obtain a
    ``QColor`` at render-time so that trace and port colours follow the
    current theme automatically.
    """
    _by_name: ClassVar[Dict[str, PortType]] = {}
    _by_id: ClassVar[Dict[int, PortType]] = {}
    _cast_registry: ClassVar[Dict[Tuple[int, int], Optional[ConverterFunc]]] = {}
    _next_id: ClassVar[int] = 200  # Auto IDs start above built-in range (0-199)

    # Auto-incrementing colour-palette index for custom types.
    # The default palette has 256 entries (indices 0-255).
    # Custom types that do not specify an explicit index get 256+.
    _next_color_index: ClassVar[int] = 256

    @classmethod
    def next_type_id(cls) -> int:
        """Return the next available type_id and increment the counter."""
        tid = cls._next_id
        cls._next_id += 1
        return tid

    @classmethod
    def next_color_index(cls) -> int:
        """Return the next available color_index and increment the counter."""
        idx = cls._next_color_index
        cls._next_color_index += 1
        return idx

    # ------------------------------------------------------------------
    # Colour resolution  (index -> QColor via the active theme palette)
    # ------------------------------------------------------------------

    @classmethod
    def resolve_color(cls, color_index: int) -> QColor:
        """
        Look up a ``QColor`` from the current theme's ``trace_color_palette``.

        Falls back to a neutral gray when the StyleManager is not yet
        available (e.g. during early import) or the index is out of range.
        """
        try:
            from weave.stylemanager import StyleManager, StyleCategory
            palette = StyleManager.instance().get(
                StyleCategory.TRACE, 'trace_color_palette'
            )
            if palette and 0 <= color_index < len(palette):
                c = palette[color_index]
                return c if isinstance(c, QColor) else QColor(*c)
        except Exception:
            pass
        # Hard fallback - neutral gray
        return QColor(128, 128, 128, 255)

    @classmethod
    def register(cls,
                 name: str,
                 color_index: Optional[int] = None,
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

        # Auto-assign colour index if not provided
        if color_index is None:
            color_index = cls.next_color_index()

        # Collision guards
        if type_id in cls._by_id:
            raise ValueError(f"type_id {type_id} already registered to '{cls._by_id[type_id].name}'")
        if lower_name in cls._by_name:
            raise ValueError(f"Port name '{name}' already registered")

        # Determine default factory
        fact = default if callable(default) else (lambda: default)

        new_type = PortType(
            name=name,
            color_index=color_index,
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
#    0  = Generic           (accepts everything - the universal fallback)
#    1  = Dummy             (visual-only - minimised node summary ports)
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
#   51  = JSON              (serialized interchange - wraps dict/list)
#
#  BINARY / RAW
#   60  = Bytes
#
#  FLOW CONTROL
#   70  = Exec              (execution trigger - carries no data)
#
#  ARRAYS / DATAFRAMES
#   80  = NdArray           (numpy - optional)
#   81  = DataFrame         (polars - optional)
#
#  IMAGE
#  100  = Image             (abstract parent for all image types)
#  101  = QImage            (PySide6 QImage)
#  102  = QPixmap           (PySide6 QPixmap)
#  103  = PILImage          (Pillow - optional)
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
#
# BUILT-IN COLOUR INDEX MAP  (into trace_color_palette)
# ============================================================================
#
#  See color_index_mapping.md for the full reference table with
#  target colours, matched palette entries, and distance metrics.
#
#   Palette
#   Index   Port Type       Palette Colour
#   ------  ---------       -----------------------
#      9    Generic         [135, 135, 135, 255]
#      8    Dummy           [120, 120, 120, 255]
#      7    Number          [105, 105, 105, 255]
#    125    Float           [  0, 255, 127, 255]
#    166    Int             [  0, 127, 255, 255]


#    166    Float           [  0, 127, 255, 255]
#    125    Int             [  0, 255, 127, 255]
#     24    Bool            [255,  76,  76, 255]
#     59    String          [255, 191,   0, 255]
#    221    Collection      [156,  89, 178, 255]
#    207    List            [165,  76, 255, 255]
#    204    Tuple           [113,  59, 222, 255]
#    186    Set             [ 89,  89, 178, 255]
#    139    Dict            [ 54, 217, 190, 255]
#    140    JSON            [ 59, 222, 195, 255]
#    185    Bytes           [ 59,  86, 222, 255]
#     17    Exec            [255, 255, 255, 255]
#    135    NdArray         [  0, 255, 191, 255]
#     36    DataFrame       [222, 113,  59, 255]
#    223    Image           [190,  54, 217, 255]
#    222    QImage          [195,  59, 222, 255]
#    217    QPixmap         [210,  76, 255, 255]
#    240    PILImage        [217,  54, 163, 255]
#     58    DateTime        [255, 210,  76, 255]
#    237    Color           [255,  76, 210, 255]
#     62    Path            [178, 156,  89, 255]
#     64    Enum            [217, 190,  54, 255]
#     35    Regex           [255, 121,  76, 255]
#     23    Error           [255,   0,   0, 255]
#
#   256+   Reserved for user-registered custom types
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
        "Generic", color_index=9, type_id=0, python_type=object
    )

    # --- Dummy (visual-only port used by minimised node summary slots) ---
    DUMMY = _reg(
        "Dummy", color_index=8, type_id=1, python_type=None,
        base_type_id=-1
    )

    # --- Numeric ---
    NUMBER = _reg(
        "Number", color_index=7, type_id=10, python_type=object,
        base_type_id=0
    )

    FLOAT = _reg(
        "Float", color_index=166, type_id=11, python_type=float,
        base_type_id=10,
        default=0.0,
        formatter=lambda x: f"{float(x):.2f}"
    )

    INT = _reg(
        "Int", color_index=125, type_id=12, python_type=int,
        base_type_id=10,
        default=0,
        casts_to={11: float}  # Int -> Float
    )

    # --- Boolean ---
    BOOL = _reg(
        "Bool", color_index=24, type_id=20, python_type=bool,
        default=False,
        casts_to={
            12: int,    # Bool -> Int  (True=1)
            11: float,  # Bool -> Float (True=1.0)
            30: str,    # Bool -> String ("True"/"False")
        }
    )

    # --- String ---
    STRING = _reg(
        "String", color_index=59, type_id=30, python_type=str,
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
        "Collection", color_index=221, type_id=40, python_type=object,
        base_type_id=0,
        formatter=lambda x: f"Collection({len(x)} items)" if hasattr(x, '__len__') else str(x)
    )

    LIST = _reg(
        "List", color_index=207, type_id=41, python_type=list,
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
        "Tuple", color_index=204, type_id=42, python_type=tuple,
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
        "Set", color_index=186, type_id=43, python_type=set,
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
        "Dict", color_index=139, type_id=50, python_type=dict,
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

    # --- JSON (serialized interchange string - wraps dict/list) ---
    JSON = _reg(
        "JSON", color_index=140, type_id=51, python_type=str,
        default=lambda: "{}",
        validator=lambda x: _is_valid_json(x),
        formatter=lambda x: f"JSON({len(x)} chars)" if isinstance(x, str) else str(x),
        casts_to={
            50: lambda j: json.loads(j),      # JSON -> Dict
            41: lambda j: json.loads(j) if isinstance(json.loads(j), list) else list(json.loads(j).items()),  # JSON -> List
            30: lambda j: j,                   # JSON -> String (identity)
        }
    )

    # ========================================================================
    # BINARY / RAW
    # ========================================================================

    BYTES = _reg(
        "Bytes", color_index=185, type_id=60, python_type=bytes,
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
        "Exec", color_index=17, type_id=70, python_type=type(None),
        default=None,
        formatter=lambda x: "▶",
    )

    # ========================================================================
    # ARRAYS / DATAFRAMES (optional - guarded imports)
    # ========================================================================

    # --- Numpy NdArray ---
    try:
        import numpy as np
        NDARRAY = _reg(
            "NdArray", color_index=135, type_id=80, python_type=np.ndarray,
            default=lambda: np.empty(0),
            formatter=lambda x: f"NdArray{x.shape} {x.dtype}",
            casts_to={
                41: lambda x: x.tolist(),                                          # NdArray -> List
                42: lambda x: tuple(x.tolist()),                                   # NdArray -> Tuple
                #11: lambda x: float(x) if x.size == 1 else float(x.flat[0]),      # NdArray -> Float
                #12: lambda x: int(x) if x.size == 1 else int(x.flat[0]),          # NdArray -> Int
                30: lambda x: str(x),                                              # NdArray -> String
            }
        )
        # Reverse casts into NdArray
        _cast[(41, 80)] = lambda x: np.array(x)    # List  -> NdArray
        _cast[(42, 80)] = lambda x: np.array(x)    # Tuple -> NdArray
        _cast[(11, 80)] = lambda x: np.array(x)    # Float -> NdArray
        _cast[(12, 80)] = lambda x: np.array(x)    # Int -> NdArray
    except ImportError:
        pass

    # --- Polars DataFrame ---
    try:
        import polars as pl
        DATAFRAME = _reg(
            "DataFrame", color_index=36, type_id=81, python_type=pl.DataFrame,
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
            _cast[(80, 81)] = lambda arr: pl.DataFrame({f"col_{i}": arr[:, i] for i in range(arr.shape[1])} if arr.ndim == 2 else {"col_0": arr})
            _cast[(81, 80)] = lambda df: df.to_numpy()
    except ImportError:
        pass

    # ========================================================================
    # IMAGE TYPES
    # ========================================================================

    # --- Abstract Image Parent ---
    IMAGE = _reg(
        "Image", color_index=223, type_id=100, python_type=object,
        base_type_id=0,
        formatter=lambda x: "Image"
    )

    # --- QImage (PySide6) ---
    QIMAGE = _reg(
        "QImage", color_index=222, type_id=101, python_type=QImage,
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
        "QPixmap", color_index=217, type_id=102, python_type=QPixmap,
        base_type_id=100,
        default=QPixmap,
        formatter=lambda x: f"QPixmap({x.width()}×{x.height()})" if isinstance(x, QPixmap) else str(x),
        casts_to={
            101: lambda px: px.toImage(),              # QPixmap -> QImage
            60: lambda px: _qimage_to_bytes(px.toImage()),
        }
    )

    # --- PIL Image (optional) ---
    try:
        from PIL import Image as PILImageLib
        PILIMAGE = _reg(
            "PILImage", color_index=240, type_id=103, python_type=PILImageLib.Image,
            base_type_id=100,
            default=lambda: PILImageLib.new('RGBA', (1, 1)),
            formatter=lambda x: f"PILImage({x.width}×{x.height} {x.mode})" if hasattr(x, 'mode') else str(x),
            casts_to={
                30: lambda img: f"PILImage({img.width}×{img.height} {img.mode})",
            }
        )

        # PIL <-> QImage cross-casts
        _cast[(103, 101)] = lambda img: _pil_to_qimage(img)
        _cast[(101, 103)] = lambda img: _qimage_to_pil(img)

        # PIL <-> NdArray (if numpy available)
        if 80 in PortRegistry._by_id:
            import numpy as np
            _cast[(103, 80)] = lambda img: np.array(img)
            _cast[(80, 103)] = lambda arr: PILImageLib.fromarray(arr.astype('uint8'))
    except ImportError:
        pass

    # ========================================================================
    # UTILITIES
    # ========================================================================

    # --- DateTime ---
    DATETIME = _reg(
        "DateTime", color_index=58, type_id=110, python_type=datetime,
        default=datetime.now,
        formatter=lambda x: x.strftime("%Y-%m-%d %H:%M:%S") if isinstance(x, datetime) else str(x),
        casts_to={
            30: lambda dt: dt.isoformat(),
            11: lambda dt: dt.timestamp(),
            12: lambda dt: int(dt.timestamp()),
        }
    )
    _cast[(30, 110)] = lambda s: datetime.fromisoformat(s)

    # --- Color (QColor) ---
    COLOR = _reg(
        "Color", color_index=237, type_id=111, python_type=QColor,
        default=lambda: QColor(255, 255, 255),
        formatter=lambda c: c.name() if isinstance(c, QColor) else str(c),
        casts_to={
            30: lambda c: c.name(),
            41: lambda c: [c.red(), c.green(), c.blue(), c.alpha()],
            42: lambda c: (c.red(), c.green(), c.blue(), c.alpha()),
            50: lambda c: {"r": c.red(), "g": c.green(), "b": c.blue(), "a": c.alpha()},
        }
    )
    _cast[(30, 111)] = lambda s: QColor(s)
    _cast[(41, 111)] = lambda lst: QColor(*lst[:4])
    _cast[(42, 111)] = lambda t: QColor(*t[:4])

    # --- Path / FilePath ---
    FILEPATH = _reg(
        "Path", color_index=62, type_id=120, python_type=Path,
        default=lambda: Path("."),
        formatter=lambda p: str(p),
        casts_to={
            30: lambda p: str(p),
            60: lambda p: p.read_bytes() if p.is_file() else b"",
        }
    )
    _cast[(30, 120)] = lambda s: Path(s)

    # --- Enum / Choice ---
    ENUM = _reg(
        "Enum", color_index=64, type_id=121, python_type=Enum,
        default=None,
        formatter=lambda e: f"{e.name}={e.value}" if isinstance(e, Enum) else str(e),
        casts_to={
            30: lambda e: e.name if isinstance(e, Enum) else str(e),
            12: lambda e: e.value if isinstance(e, Enum) and isinstance(e.value, int) else 0,
        }
    )

    # --- Regex (compiled pattern) ---
    REGEX = _reg(
        "Regex", color_index=35, type_id=122, python_type=re.Pattern,
        default=lambda: re.compile(""),
        formatter=lambda r: f"re({r.pattern!r})" if isinstance(r, re.Pattern) else str(r),
        casts_to={
            30: lambda r: r.pattern,
        }
    )
    _cast[(30, 122)] = lambda s: re.compile(s)

    # --- Error / Result ---
    ERROR = _reg(
        "Error", color_index=23, type_id=123, python_type=Exception,
        default=None,
        formatter=lambda e: f"Error({type(e).__name__}: {e})" if isinstance(e, Exception) else str(e),
        casts_to={
            30: lambda e: f"{type(e).__name__}: {e}",
            20: lambda e: False,
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
