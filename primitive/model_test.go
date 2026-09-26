package primitive_test

import (
	"image"
	"image/color"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func checkerboard(size int) image.Image {
	im := image.NewRGBA(image.Rect(0, 0, size, size))
	for y := 0; y < size; y++ {
		for x := 0; x < size; x++ {
			c := color.White
			if (x/4+y/4)%2 == 0 {
				c = color.Black
			}
			im.Set(x, y, c)
		}
	}
	return im
}

func TestModelStepImprovesScore(t *testing.T) {
	target := checkerboard(16)
	background := primitive.MakeColor(primitive.AverageImageColor(target))
	model := primitive.NewModel(target, background, 16, 1)

	before := model.Score
	model.Step(primitive.ShapeTypeTriangle, 128, 0)
	after := model.Score

	if len(model.Shapes) != 1 {
		t.Fatalf("expected 1 shape after one Step, got %d", len(model.Shapes))
	}
	if after >= before {
		t.Errorf("Step did not improve score: before=%f after=%f", before, after)
	}
}

func TestModelSVGProducesOutput(t *testing.T) {
	target := checkerboard(8)
	background := primitive.MakeColor(primitive.AverageImageColor(target))
	model := primitive.NewModel(target, background, 8, 1)
	model.Step(primitive.ShapeTypeTriangle, 128, 0)

	svg := model.SVG()
	if svg == "" {
		t.Fatal("SVG() returned empty string")
	}
}
