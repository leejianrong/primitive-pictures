package primitive_test

import (
	"image"
	"math/rand"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func TestMeasureContinuityCountsAddedAndDropped(t *testing.T) {
	a, b, c, d := &primitive.Triangle{}, &primitive.Triangle{}, &primitive.Triangle{}, &primitive.Triangle{}
	got := primitive.MeasureContinuity([]primitive.Shape{a, b, c}, []primitive.Shape{a, c, d})
	want := primitive.Continuity{Prev: 3, Cur: 3, Carried: 2, Added: 1, Dropped: 1}
	if got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	if f := got.CarriedFraction(); f < 0.6666 || f > 0.6667 {
		t.Errorf("CarriedFraction = %v, want 2/3", f)
	}
	if f := got.AddedFraction(); f < 0.3333 || f > 0.3334 {
		t.Errorf("AddedFraction = %v, want 1/3", f)
	}
}

func TestMeasureContinuityIdenticalListsAreFullyCarried(t *testing.T) {
	shapes := []primitive.Shape{&primitive.Triangle{}, &primitive.Triangle{}}
	got := primitive.MeasureContinuity(shapes, shapes)
	if got.CarriedFraction() != 1 || got.AddedFraction() != 0 || got.Dropped != 0 {
		t.Errorf("identical lists: %+v", got)
	}
}

func TestMeasureContinuityFirstFrameIsAllAdded(t *testing.T) {
	got := primitive.MeasureContinuity(nil, []primitive.Shape{&primitive.Triangle{}})
	if got.AddedFraction() != 1 || got.Carried != 0 {
		t.Errorf("first frame: %+v", got)
	}
	if primitive.MeasureContinuity(nil, nil).CarriedFraction() != 0 {
		t.Error("empty frame should report 0, not NaN")
	}
}

// End to end: through ProcessSequence, a frame that drops 5 of 10 and adds 5
// reports exactly half carried; a bigger new-shape budget lowers the fraction.
func TestProcessSequenceReportsContinuityMovingWithBudget(t *testing.T) {
	frames := []image.Image{shifted(16, 0), shifted(16, 1), shifted(16, 2)}
	last := func(initial, add int, drop float64) primitive.Continuity {
		cfg := seqConfig(frames[0], initial, add)
		cfg.DropRate = drop
		cfg.Rnd = rand.New(rand.NewSource(1))
		var c primitive.Continuity
		err := primitive.ProcessSequence(len(frames),
			func(i int) (image.Image, error) { return frames[i], nil }, cfg,
			func(r primitive.FrameResult) error { c = r.Continuity; return nil })
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	half := last(10, 5, 0.5)
	if half.Carried != 5 || half.Added != 5 || half.Dropped != 5 || half.CarriedFraction() != 0.5 {
		t.Errorf("drop 5 add 5 of 10: %+v", half)
	}
	steady := last(10, 2, 0.2)  // carried 8 of 10
	dynamic := last(10, 6, 0.6) // carried 4 of 10
	if !(steady.CarriedFraction() > dynamic.CarriedFraction()) {
		t.Errorf("higher budget should carry less: steady=%v dynamic=%v",
			steady.CarriedFraction(), dynamic.CarriedFraction())
	}
}
