package primitive_test

import (
	"fmt"
	"image"
	"image/color"
	"math/rand"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func shifted(size, dx int) image.Image {
	im := image.NewRGBA(image.Rect(0, 0, size, size))
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			c := color.White
			if ((x+dx)/4+y/4)%2 == 0 {
				c = color.Black
			}
			im.Set(x, y, c)
		}
	}
	return im
}

func seqConfig(target image.Image, initial, perFrame int) primitive.SequenceConfig {
	return primitive.SequenceConfig{
		Size:              16,
		Workers:           1,
		Background:        primitive.MakeColor(primitive.AverageImageColor(target)),
		ShapeType:         primitive.ShapeTypeTriangle,
		Alpha:             128,
		InitialShapes:     initial,
		NewShapesPerFrame: perFrame,
	}
}

func TestNewModelFromCarriesShapesExactly(t *testing.T) {
	first := shifted(16, 0)
	prev := primitive.NewModel(first, primitive.MakeColor(primitive.AverageImageColor(first)), 16, 1)
	for i := 0; i < 5; i++ {
		prev.Step(primitive.ShapeTypeTriangle, 128, 0)
	}

	// Seeding against the same target must reproduce prev, score included.
	got, err := primitive.NewModelFrom(prev, first)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Shapes) != len(prev.Shapes) {
		t.Fatalf("carried %d shapes, want %d", len(got.Shapes), len(prev.Shapes))
	}
	for i := range prev.Shapes {
		if fmt.Sprintf("%+v", got.Shapes[i]) != fmt.Sprintf("%+v", prev.Shapes[i]) {
			t.Errorf("shape %d changed: %+v vs %+v", i, got.Shapes[i], prev.Shapes[i])
		}
		if got.Colors[i] != prev.Colors[i] {
			t.Errorf("color %d changed", i)
		}
	}
	if d := got.Score - prev.Score; d > 1e-9 || d < -1e-9 {
		t.Errorf("score = %v, want %v", got.Score, prev.Score)
	}
	if string(got.Current.Pix) != string(prev.Current.Pix) {
		t.Error("replayed canvas differs from the previous frame's")
	}

	// A different target keeps the shapes but rescores them.
	next, err := primitive.NewModelFrom(prev, shifted(16, 2))
	if err != nil {
		t.Fatal(err)
	}
	if len(next.Shapes) != len(prev.Shapes) || next.Score == prev.Score {
		t.Errorf("expected same shapes and a new score, got %d shapes, score %v vs %v",
			len(next.Shapes), next.Score, prev.Score)
	}
}

func TestNewModelFromRejectsSizeChange(t *testing.T) {
	prev := primitive.NewModel(shifted(16, 0), primitive.Color{}, 16, 1)
	if _, err := primitive.NewModelFrom(prev, shifted(8, 0)); err == nil {
		t.Fatal("expected an error for a frame of a different size")
	}
}

func TestProcessSequenceRespectsBudgets(t *testing.T) {
	frames := []image.Image{shifted(16, 0), shifted(16, 1), shifted(16, 2)}
	var got []primitive.FrameResult
	var counts []int
	err := primitive.ProcessSequence(len(frames),
		func(i int) (image.Image, error) { return frames[i], nil },
		seqConfig(frames[0], 4, 2),
		func(r primitive.FrameResult) error {
			got = append(got, r)
			counts = append(counts, len(r.Model.Shapes))
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}
	wantCarried := []int{0, 4, 6}
	wantAdded := []int{4, 2, 2}
	for i, r := range got {
		if r.Index != i || r.Carried != wantCarried[i] || r.Added != wantAdded[i] {
			t.Errorf("frame %d: index=%d carried=%d added=%d, want carried=%d added=%d",
				i, r.Index, r.Carried, r.Added, wantCarried[i], wantAdded[i])
		}
	}
	if counts[2] != 8 {
		t.Errorf("final frame has %d shapes, want 8", counts[2])
	}
}

func TestProcessSequenceStopsOnCallbackError(t *testing.T) {
	frames := []image.Image{shifted(16, 0), shifted(16, 1), shifted(16, 2)}
	seen := 0
	err := primitive.ProcessSequence(3,
		func(i int) (image.Image, error) { return frames[i], nil },
		seqConfig(frames[0], 1, 1),
		func(r primitive.FrameResult) error {
			seen++
			if r.Index == 1 {
				return fmt.Errorf("boom")
			}
			return nil
		})
	if err == nil || seen != 2 {
		t.Fatalf("err=%v seen=%d, want an error after frame 1", err, seen)
	}
}

func TestProcessSequenceDropsOldShapesAndSettles(t *testing.T) {
	frames := make([]image.Image, 6)
	for i := range frames {
		frames[i] = shifted(16, i)
	}
	cfg := seqConfig(frames[0], 10, 5)
	cfg.DropRate = 0.5
	cfg.Rnd = rand.New(rand.NewSource(1))
	var got []primitive.FrameResult
	err := primitive.ProcessSequence(len(frames),
		func(i int) (image.Image, error) { return frames[i], nil }, cfg,
		func(r primitive.FrameResult) error { got = append(got, r); return nil })
	if err != nil {
		t.Fatal(err)
	}
	// 10 -> drop 5, add 5 -> 10 ...: steady at NewShapesPerFrame/DropRate.
	for i, r := range got[1:] {
		if r.Dropped != 5 || r.Carried != 5 || r.Added != 5 || len(r.Model.Shapes) != 10 {
			t.Errorf("frame %d: dropped=%d carried=%d added=%d total=%d", i+1,
				r.Dropped, r.Carried, r.Added, len(r.Model.Shapes))
		}
	}
}

func TestProcessSequenceRejectsBadDropRate(t *testing.T) {
	cfg := seqConfig(shifted(16, 0), 1, 1)
	cfg.DropRate = 1.5
	err := primitive.ProcessSequence(1, func(int) (image.Image, error) { return shifted(16, 0), nil }, cfg,
		func(primitive.FrameResult) error { return nil })
	if err == nil {
		t.Fatal("expected an error for DropRate > 1")
	}
}
