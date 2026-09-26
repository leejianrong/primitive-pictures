BIN := bin
GOPKGDIRS := $(shell go list -f '{{.Dir}}' ./... 2>/dev/null)

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: fmtcheck
fmtcheck: ## Fail if any file is not gofmt-formatted
	@if [ -z "$(GOPKGDIRS)" ]; then echo "go list produced no packages"; exit 1; fi
	@out=$$(gofmt -l $(GOPKGDIRS)); if [ -n "$$out" ]; then echo "$$out"; echo "not gofmt-formatted"; exit 1; fi

.PHONY: vet
vet: ## Run go vet
	go vet ./...

.PHONY: lint
lint: ## Run golangci-lint
	GOLANGCI_LINT_CACHE=$(CURDIR)/$(BIN)/golangci-lint-cache golangci-lint run ./...

.PHONY: tidycheck
tidycheck: ## Fail if go.mod/go.sum are not tidy
	@cp go.mod go.mod.bak; [ -f go.sum ] && cp go.sum go.sum.bak || true; \
	go mod tidy; status=0; \
	diff -q go.mod go.mod.bak >/dev/null || { echo "go.mod not tidy"; status=1; }; \
	[ -f go.sum.bak ] && { diff -q go.sum go.sum.bak >/dev/null || { echo "go.sum not tidy"; status=1; }; }; \
	mv go.mod.bak go.mod; [ -f go.sum.bak ] && mv go.sum.bak go.sum || true; \
	exit $$status

.PHONY: check
check: fmtcheck vet lint tidycheck ## Run all static gates (fmt, vet, lint, tidy)

.PHONY: test
test: ## Run fast tests with race detector, no infra required
	go test -race -count=1 -short ./...

.PHONY: test-all
test-all: ## Run the full test suite, including integration-tagged tests
	go test -race -count=1 -tags=integration ./...

.PHONY: vulncheck
vulncheck: ## Scan for known vulnerabilities reachable from this module
	govulncheck ./...

.PHONY: build
build: ## Build the primitive binary for the current platform
	mkdir -p $(BIN)
	go build -o $(BIN)/primitive .

.PHONY: xbuild
xbuild: ## Cross-compile for the platforms we claim to support
	mkdir -p $(BIN)
	GOOS=linux   GOARCH=amd64 go build -o $(BIN)/primitive-linux-amd64 .
	GOOS=linux   GOARCH=arm64 go build -o $(BIN)/primitive-linux-arm64 .
	GOOS=darwin  GOARCH=amd64 go build -o $(BIN)/primitive-darwin-amd64 .
	GOOS=darwin  GOARCH=arm64 go build -o $(BIN)/primitive-darwin-arm64 .
	GOOS=windows GOARCH=amd64 go build -o $(BIN)/primitive-windows-amd64.exe .

.PHONY: run
run: build ## Build and run against the bundled Mona Lisa example (100 triangles)
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/monalisa.out.png -n 100

.PHONY: samples
samples: build ## Generate one example output per shape mode into bin/samples/
	mkdir -p $(BIN)/samples
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/triangle.png  -m 1 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/rectangle.png -m 2 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/ellipse.png   -m 3 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/circle.png    -m 4 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/rotatedrect.png -m 5 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/bezier.png    -m 6 -n 100 -rep 5
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/polygon.png   -m 8 -n 100
	$(BIN)/primitive -i examples/monalisa.png -o $(BIN)/samples/stipple.png   -m 9 -n 600 -a 200 -grid 10 -jitter 3
	@echo "wrote $(BIN)/samples/*.png"

.PHONY: python-check
python-check: ## Lint + format-check the pipeline/ Python package
	cd pipeline && uv run ruff check . && uv run ruff format --check .

.PHONY: python-test
python-test: ## Run the pipeline/ Python package's fast tests (no RunPod/network)
	cd pipeline && uv run python -m pytest

.PHONY: ci
ci: check test-all xbuild vulncheck python-check python-test ## Everything CI runs

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf $(BIN)
