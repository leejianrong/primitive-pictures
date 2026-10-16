package primitive

import (
	"fmt"
	"image"
	"math/rand"
	"sort"
	"time"
)

// SequenceConfig drives ProcessSequence (ADR-0007, ADR-0009).
type SequenceConfig struct {
	Size       int // output size, as for NewModel
	Workers    int
	Background Color // shared by every frame so carried shapes composite identically
	ShapeType  ShapeType
	Alpha      int
	Repeat     int

	// InitialShapes is the cold-start budget for the first frame processed;
	// NewShapesPerFrame is the budget for each later one.
	//
	// DropRate is the fraction (0..1) of the previous frame's shapes randomly
	// discarded before the new ones are searched, so the new shapes can cover
	// the holes and the count settles near NewShapesPerFrame/DropRate instead
	// of growing without bound. 0 never drops; 1 is equivalent to a cold start
	// each frame. Rnd picks which shapes go (nil = time-seeded).
	InitialShapes     int
	NewShapesPerFrame int
	DropRate          float64
	Rnd               *rand.Rand

	StippleGrid   int
	StippleJitter int
}

// FrameResult is one finished frame. Model stays valid only until the
// OnFrame callback returns; ProcessSequence keeps it as the next frame's seed.
type FrameResult struct {
	Index   int
	Model   *Model
	Carried int // shapes inherited unchanged from the previous frame (after drops)
	Dropped int // previous-frame shapes discarded before searching
	Added   int // shapes newly searched for this frame
	Elapsed time.Duration
}

// ProcessSequence runs frames [0, count) in order. load(i) returns frame i's
// target image. Frame 0 starts cold; every later frame is seeded from the
// previous frame's finished shapes (NewModelFrom) and then adds exactly
// NewShapesPerFrame new shapes with the unchanged Model.Step. onFrame runs as
// each frame completes, so callers can write output incrementally; an error
// from load or onFrame stops the run.
func ProcessSequence(count int, load func(i int) (image.Image, error), cfg SequenceConfig, onFrame func(FrameResult) error) error {
	if cfg.InitialShapes < 1 {
		return fmt.Errorf("InitialShapes must be > 0, got %d", cfg.InitialShapes)
	}
	if cfg.NewShapesPerFrame < 0 {
		return fmt.Errorf("NewShapesPerFrame must be >= 0, got %d", cfg.NewShapesPerFrame)
	}
	if cfg.DropRate < 0 || cfg.DropRate > 1 {
		return fmt.Errorf("DropRate must be in [0, 1], got %v", cfg.DropRate)
	}
	rnd := cfg.Rnd
	if rnd == nil {
		rnd = rand.New(rand.NewSource(time.Now().UnixNano()))
	}
	var prev *Model
	for i := 0; i < count; i++ {
		start := time.Now()
		target, err := load(i)
		if err != nil {
			return fmt.Errorf("frame %d: %w", i, err)
		}
		var model *Model
		carried, dropped, budget := 0, 0, cfg.InitialShapes
		if prev == nil {
			model = NewModel(target, cfg.Background, cfg.Size, cfg.Workers)
		} else {
			keep := keepIndices(len(prev.Shapes), cfg.DropRate, rnd)
			if model, err = newModelFromSubset(prev, target, keep); err != nil {
				return fmt.Errorf("frame %d: %w", i, err)
			}
			carried, dropped, budget = len(keep), len(prev.Shapes)-len(keep), cfg.NewShapesPerFrame
		}
		for _, w := range model.Workers {
			w.StippleGrid = cfg.StippleGrid
			w.StippleJitter = cfg.StippleJitter
		}
		for j := 0; j < budget; j++ {
			model.Step(cfg.ShapeType, cfg.Alpha, cfg.Repeat)
		}
		res := FrameResult{Index: i, Model: model, Carried: carried, Dropped: dropped, Added: len(model.Shapes) - carried, Elapsed: time.Since(start)}
		if err := onFrame(res); err != nil {
			return err
		}
		prev = model
	}
	return nil
}

// NewModelFrom builds a Model for target seeded with prev's finished shapes
// and colors, replayed exactly as-is (static carry-forward, ADR-0009). Scores
// and Score are recomputed against the new target. Background, output size and
// worker count come from prev, so target must have prev's dimensions.
func NewModelFrom(prev *Model, target image.Image) (*Model, error) {
	keep := make([]int, len(prev.Shapes))
	for i := range keep {
		keep[i] = i
	}
	return newModelFromSubset(prev, target, keep)
}

// keepIndices returns, in ascending (z-)order, the indices of n shapes that
// survive after discarding round(rate*n) of them uniformly at random.
func keepIndices(n int, rate float64, rnd *rand.Rand) []int {
	drop := int(rate*float64(n) + 0.5)
	keep := rnd.Perm(n)[:n-drop]
	sort.Ints(keep)
	return keep
}

// newModelFromSubset is NewModelFrom replaying only prev's shapes at the
// given ascending indices; their colors are kept as-is, not re-fitted.
func newModelFromSubset(prev *Model, target image.Image, keep []int) (*Model, error) {
	if target.Bounds().Size() != prev.Target.Bounds().Size() {
		return nil, fmt.Errorf("frame size %v differs from previous frame %v",
			target.Bounds().Size(), prev.Target.Bounds().Size())
	}
	model := NewModel(target, prev.Background, prev.Sw, len(prev.Workers))
	// Copy the output geometry from prev rather than re-deriving it, so the
	// replay is pixel-identical whatever the aspect ratio.
	model.Sw, model.Sh, model.Scale = prev.Sw, prev.Sh, prev.Scale
	model.Context = model.newContext()
	for _, i := range keep {
		shape := prev.Shapes[i].Copy()
		color := prev.Colors[i]
		before := copyRGBA(model.Current)
		lines := shape.Rasterize()
		drawLines(model.Current, color, lines)
		model.Score = differencePartial(model.Target, before, model.Current, model.Score, lines)
		model.Shapes = append(model.Shapes, shape)
		model.Colors = append(model.Colors, color)
		model.Scores = append(model.Scores, model.Score)
		model.Context.SetRGBA255(color.R, color.G, color.B, color.A)
		shape.Draw(model.Context, model.Scale)
	}
	return model, nil
}
