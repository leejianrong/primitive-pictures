package primitive_test

import (
	"image/color"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func TestMakeHexColor(t *testing.T) {
	cases := []struct {
		name string
		hex  string
		want primitive.Color
	}{
		{"3-digit", "#0f8", primitive.Color{R: 0, G: 255, B: 136, A: 255}},
		{"4-digit with alpha", "0f8c", primitive.Color{R: 0, G: 255, B: 136, A: 204}},
		{"6-digit", "#112233", primitive.Color{R: 17, G: 34, B: 51, A: 255}},
		{"8-digit with alpha", "11223344", primitive.Color{R: 17, G: 34, B: 51, A: 68}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := primitive.MakeHexColor(c.hex)
			if got != c.want {
				t.Errorf("MakeHexColor(%q) = %+v, want %+v", c.hex, got, c.want)
			}
		})
	}
}

func TestMakeColor(t *testing.T) {
	got := primitive.MakeColor(color.NRGBA{R: 10, G: 20, B: 30, A: 255})
	want := primitive.Color{R: 10, G: 20, B: 30, A: 255}
	if got != want {
		t.Errorf("MakeColor() = %+v, want %+v", got, want)
	}
}
