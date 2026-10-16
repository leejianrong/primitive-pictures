package main

import (
	"encoding/json"
	"image"
	"image/color"
	"os"
	"path/filepath"
	"testing"

	"github.com/leejianrong/primitive-pictures/primitive"
)

func writeFrames(t *testing.T, dir string, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		im := image.NewRGBA(image.Rect(0, 0, 16, 16))
		for y := 0; y < 16; y++ {
			for x := 0; x < 16; x++ {
				if (x+i)/4%2 == 0 {
					im.Set(x, y, color.Black)
				} else {
					im.Set(x, y, color.White)
				}
			}
		}
		if err := primitive.SavePNG(filepath.Join(dir, string(rune('a'+i))+".png"), im); err != nil {
			t.Fatal(err)
		}
	}
}

func setFramesGlobals() {
	InputSize, OutputSize, Workers = 16, 16, 1
	Configs = shapeConfigArray{{Count: 3, Mode: 1, Alpha: 128}}
}

func readManifest(t *testing.T, out string) []frameItem {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(out, "frames.json"))
	if err != nil {
		t.Fatal(err)
	}
	var items []frameItem
	if err := json.Unmarshal(b, &items); err != nil {
		t.Fatal(err)
	}
	return items
}

func TestRunFramesWritesEachFrameAndResumes(t *testing.T) {
	in, out := t.TempDir(), t.TempDir()
	writeFrames(t, in, 3)
	setFramesGlobals()

	if err := runFrames(in, out, 3, 2, 0, false); err != nil {
		t.Fatal(err)
	}
	items := readManifest(t, out)
	if len(items) != 3 || items[0].Added != 3 || items[1].Carried != 3 || items[2].Shapes != 7 {
		t.Fatalf("unexpected manifest: %+v", items)
	}
	for _, name := range []string{"a.png", "a.svg", "c.png", "c.svg"} {
		if _, err := os.Stat(filepath.Join(out, name)); err != nil {
			t.Errorf("missing %s: %v", name, err)
		}
	}

	// Simulate a kill after frame 1: frame 2 is gone, so only it is redone.
	_ = os.Remove(filepath.Join(out, "c.png"))
	_ = os.Remove(filepath.Join(out, "c.svg"))
	if err := runFrames(in, out, 3, 2, 0, true); err != nil {
		t.Fatal(err)
	}
	items = readManifest(t, out)
	if len(items) != 3 || items[0].Status != "resumed" || items[1].Status != "resumed" ||
		items[2].Status != "ok" || items[2].Added != 3 {
		t.Fatalf("unexpected resumed manifest: %+v", items)
	}
}

func TestListFramesRejectsEmptyDir(t *testing.T) {
	if _, err := listFrames(t.TempDir()); err == nil {
		t.Fatal("expected an error for a directory with no frames")
	}
}
