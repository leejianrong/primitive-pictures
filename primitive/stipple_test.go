package primitive_test

import (
	"image"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func newTestWorker(w, h, grid, jitter int) *primitive.Worker {
	target := image.NewRGBA(image.Rect(0, 0, w, h))
	worker := primitive.NewWorker(target)
	worker.StippleGrid = grid
	worker.StippleJitter = jitter
	return worker
}

func TestNewRandomStippleStaysWithinJitterOfAnchor(t *testing.T) {
	worker := newTestWorker(64, 64, 8, 2)
	for i := 0; i < 500; i++ {
		s := primitive.NewRandomStipple(worker)
		if dx := abs(s.X - s.AnchorX); dx > s.JitterRadius {
			t.Fatalf("X %d strayed %d from anchor %d, jitter bound is %d", s.X, dx, s.AnchorX, s.JitterRadius)
		}
		if dy := abs(s.Y - s.AnchorY); dy > s.JitterRadius {
			t.Fatalf("Y %d strayed %d from anchor %d, jitter bound is %d", s.Y, dy, s.AnchorY, s.JitterRadius)
		}
	}
}

func TestNewRandomStippleAnchorsAreGridAligned(t *testing.T) {
	grid := 8
	worker := newTestWorker(64, 64, grid, 2)
	for i := 0; i < 500; i++ {
		s := primitive.NewRandomStipple(worker)
		if (s.AnchorX-grid/2)%grid != 0 {
			t.Fatalf("AnchorX %d is not aligned to a %d-pixel grid", s.AnchorX, grid)
		}
		if (s.AnchorY-grid/2)%grid != 0 {
			t.Fatalf("AnchorY %d is not aligned to a %d-pixel grid", s.AnchorY, grid)
		}
	}
}

func TestNewRandomStippleClampsExcessiveJitter(t *testing.T) {
	grid := 8
	worker := newTestWorker(64, 64, grid, grid) // jitter == grid, well past the grid/2 cap
	for i := 0; i < 500; i++ {
		s := primitive.NewRandomStipple(worker)
		if s.JitterRadius > grid/2 {
			t.Fatalf("JitterRadius %d exceeds grid/2 (%d) for grid %d", s.JitterRadius, grid/2, grid)
		}
	}
}

func TestNewRandomStippleNeverGeneratesOutOfBounds(t *testing.T) {
	// A small canvas relative to grid/jitter exercises the clamping paths at
	// the edges, including a jitter wider than half the grid spacing.
	worker := newTestWorker(10, 10, 8, 6)
	for i := 0; i < 500; i++ {
		s := primitive.NewRandomStipple(worker)
		if s.X < 0 || s.X >= worker.W || s.Y < 0 || s.Y >= worker.H {
			t.Fatalf("out of bounds: (%d,%d) on a %dx%d canvas", s.X, s.Y, worker.W, worker.H)
		}
		if s.R < 1 {
			t.Fatalf("radius %d must be >= 1", s.R)
		}
	}
}

func TestStippleMutateStaysWithinJitterAndRadiusBounds(t *testing.T) {
	worker := newTestWorker(64, 64, 8, 2)
	s := primitive.NewRandomStipple(worker)
	for i := 0; i < 1000; i++ {
		s.Mutate()
		if dx := abs(s.X - s.AnchorX); dx > s.JitterRadius {
			t.Fatalf("Mutate moved X %d %d away from anchor %d, jitter bound is %d", s.X, dx, s.AnchorX, s.JitterRadius)
		}
		if dy := abs(s.Y - s.AnchorY); dy > s.JitterRadius {
			t.Fatalf("Mutate moved Y %d %d away from anchor %d, jitter bound is %d", s.Y, dy, s.AnchorY, s.JitterRadius)
		}
		if s.R < s.MinR || s.R > s.MaxR {
			t.Fatalf("Mutate pushed R=%d outside [%d,%d]", s.R, s.MinR, s.MaxR)
		}
	}
}

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}
