package primitive

import (
	"fmt"
	"math"

	"github.com/fogleman/gg"
)

// Stipple is a circle constrained to a jittered grid cell, for a
// pointillism/stipple mode (ADR-0003, docs/adr/0003-pointillism-constrained-random.md).
//
// Anchor{X,Y} is the cell's jittered center, fixed at construction. Mutate()
// only ever moves X,Y within JitterRadius of the anchor and R within
// [MinR,MaxR] -- unlike Ellipse.Mutate, which lets a shape drift anywhere on
// the canvas over successive hill-climb steps. That's the whole mechanism:
// reuse the existing competitive search (Model.Step/Worker, untouched), just
// constrain where a candidate shape is allowed to live.
type Stipple struct {
	Worker           *Worker
	AnchorX, AnchorY int
	X, Y             int
	R                int
	MinR, MaxR       int
	JitterRadius     int
}

// DefaultStippleGrid/DefaultStippleJitter are used when a Worker's own
// StippleGrid is unset (<=0) -- keeps NewRandomStipple safe to call even if
// the caller forgot to configure it.
const (
	DefaultStippleGrid   = 16
	DefaultStippleJitter = 3
)

func NewRandomStipple(worker *Worker) *Stipple {
	rnd := worker.Rnd

	grid := worker.StippleGrid
	if grid <= 0 {
		grid = DefaultStippleGrid
	}
	jitter := worker.StippleJitter
	if jitter < 0 {
		jitter = 0
	}
	// Keep jitter from spilling meaningfully into a neighboring cell -- that
	// would erode the "regular spacing" this mode exists for.
	if jitter*2 > grid {
		jitter = grid / 2
	}

	cols := worker.W / grid
	if cols < 1 {
		cols = 1
	}
	rows := worker.H / grid
	if rows < 1 {
		rows = 1
	}
	cellX := rnd.Intn(cols)
	cellY := rnd.Intn(rows)
	anchorX := clampInt(cellX*grid+grid/2, 0, worker.W-1)
	anchorY := clampInt(cellY*grid+grid/2, 0, worker.H-1)

	x, y := anchorX, anchorY
	if jitter > 0 {
		x = clampInt(anchorX+rnd.Intn(2*jitter+1)-jitter, 0, worker.W-1)
		y = clampInt(anchorY+rnd.Intn(2*jitter+1)-jitter, 0, worker.H-1)
	}

	minR := 1
	maxR := grid / 2
	if maxR < minR {
		maxR = minR
	}
	r := rnd.Intn(maxR-minR+1) + minR

	return &Stipple{
		Worker:       worker,
		AnchorX:      anchorX,
		AnchorY:      anchorY,
		X:            x,
		Y:            y,
		R:            r,
		MinR:         minR,
		MaxR:         maxR,
		JitterRadius: jitter,
	}
}

func (s *Stipple) Draw(dc *gg.Context, scale float64) {
	dc.DrawEllipse(float64(s.X), float64(s.Y), float64(s.R), float64(s.R))
	dc.Fill()
}

func (s *Stipple) SVG(attrs string) string {
	return fmt.Sprintf(
		"<circle %s cx=\"%d\" cy=\"%d\" r=\"%d\" />",
		attrs, s.X, s.Y, s.R)
}

func (s *Stipple) Copy() Shape {
	a := *s
	return &a
}

func (s *Stipple) Mutate() {
	rnd := s.Worker.Rnd
	switch rnd.Intn(2) {
	case 0:
		if s.JitterRadius > 0 {
			s.X = clampInt(s.AnchorX+rnd.Intn(2*s.JitterRadius+1)-s.JitterRadius, 0, s.Worker.W-1)
			s.Y = clampInt(s.AnchorY+rnd.Intn(2*s.JitterRadius+1)-s.JitterRadius, 0, s.Worker.H-1)
		}
	case 1:
		spread := float64(s.MaxR-s.MinR+1) * 0.5
		delta := int(rnd.NormFloat64() * spread)
		s.R = clampInt(s.R+delta, s.MinR, s.MaxR)
	}
}

func (s *Stipple) Rasterize() []Scanline {
	w := s.Worker.W
	h := s.Worker.H
	lines := s.Worker.Lines[:0]
	for dy := 0; dy < s.R; dy++ {
		y1 := s.Y - dy
		y2 := s.Y + dy
		if (y1 < 0 || y1 >= h) && (y2 < 0 || y2 >= h) {
			continue
		}
		sx := int(math.Sqrt(float64(s.R*s.R - dy*dy)))
		x1 := s.X - sx
		x2 := s.X + sx
		if x1 < 0 {
			x1 = 0
		}
		if x2 >= w {
			x2 = w - 1
		}
		if y1 >= 0 && y1 < h {
			lines = append(lines, Scanline{y1, x1, x2, 0xffff})
		}
		if y2 >= 0 && y2 < h && dy > 0 {
			lines = append(lines, Scanline{y2, x1, x2, 0xffff})
		}
	}
	return lines
}
