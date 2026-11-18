"""Mô hình dữ liệu của file .srtpx (thuần Python, không Qt)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Box:
    """Hộp chữ nhật theo toạ độ #res: x1/x2 là mép trái/phải, y1/y2 là mép trên/dưới.

    Gốc ở góc trên trái. Các mép là toạ độ liên tục (không phải chỉ số điểm ảnh).
    """

    x1: float
    x2: float
    y1: float
    y2: float
    # Hộp 4 góc (chữ nghiêng/chạy chéo): (ax, ay, bx, by, cx, cy, dx, dy) = trên-trái, trên-phải, dưới-phải, dưới-trái,
    # cùng toạ độ #res. Khi có, x1..y2 LUÔN là hộp bao của 4 góc (mọi chỗ chỉ cần hộp chữ nhật vẫn chạy đúng).
    quad: tuple[float, ...] | None = None

    @classmethod
    def from_quad(cls, quad: "tuple[float, ...] | list[float]") -> "Box":
        """Hộp 4 góc từ 8 số; x1..y2 = hộp bao."""
        q = tuple(float(v) for v in quad)
        if len(q) != 8:
            raise ValueError("Hộp 4 góc cần đúng 8 số (ax ay bx by cx cy dx dy).")
        xs, ys = q[0::2], q[1::2]
        return cls(min(xs), max(xs), min(ys), max(ys), q)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


@dataclass(frozen=True)
class Keyframe:
    """Hộp tại một thời điểm (giây, tính từ đầu video)."""

    time: float
    box: Box


@dataclass
class Subtitle:
    """Một phụ đề.

    Bất biến: đúng MỘT trong hai điều sau đúng:
      - `keyframes` không rỗng (đã sắp theo thời gian, không trùng giờ) và `box` là None;
      - `keyframes` rỗng và `box` là hộp cố định.
    Nhờ vậy mô hình luôn ở dạng chuẩn và round-trip đọc -> ghi -> đọc cho kết quả y hệt.
    """

    index: int
    start: float
    end: float
    text: str
    box: Box | None = None
    keyframes: list[Keyframe] = field(default_factory=list)

    def validate(self) -> None:
        """Ném ValueError nếu vi phạm bất biến ở trên."""
        if self.keyframes and self.box is not None:
            raise ValueError("Phụ đề có keyframe thì không được có hộp cố định.")
        if not self.keyframes and self.box is None:
            raise ValueError("Phụ đề phải có hộp cố định hoặc ít nhất một keyframe.")
        times = [k.time for k in self.keyframes]
        if times != sorted(times) or len(set(times)) != len(times):
            raise ValueError("Keyframe phải sắp theo thời gian và không trùng giờ.")

    @property
    def first_box(self) -> Box:
        """Hộp dùng khi ghi file: keyframe đầu nếu có, nếu không thì hộp cố định."""
        if self.keyframes:
            return self.keyframes[0].box
        assert self.box is not None
        return self.box


@dataclass
class SrtpxDocument:
    """Toàn bộ file: độ phân giải đo toạ độ (nếu có) và danh sách phụ đề."""

    res: tuple[int, int] | None = None
    subtitles: list[Subtitle] = field(default_factory=list)


@dataclass(frozen=True)
class ParseWarning:
    """Một cảnh báo/lỗi khi đọc file.

    `line` là số dòng (tính từ 1) trong file; `block_skipped` là True nếu cả block bị bỏ.
    """

    line: int
    message: str
    block_skipped: bool = False

    def __str__(self) -> str:
        prefix = "Bỏ qua block" if self.block_skipped else "Cảnh báo"
        return f"{prefix} (dòng {self.line}): {self.message}"


@dataclass
class ParseResult:
    document: SrtpxDocument
    warnings: list[ParseWarning] = field(default_factory=list)
