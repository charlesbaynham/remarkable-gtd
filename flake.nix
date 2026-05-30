{
  description = "remarkable-gtd — GTD paper workflow for reMarkable 2";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        rmapi = pkgs.rmapi.overrideAttrs (old: {
          version = "0.0.33-unstable-2026-05-30";
          src = pkgs.fetchFromGitHub {
            owner = "ddvk";
            repo = "rmapi";
            rev = "0a69a608b22f5c11dff07254a7e30b45f2c041f7";
            sha256 = "sha256-g7KFLa+VBkubzdrgMFDVvAuscw41nyfHd7DWvh3S+NU=";
          };
        });
      in
      {
        devShells.default = pkgs.mkShell {
          name = "remarkable-gtd";

          packages = with pkgs; [
            # Python package manager
            uv

            # System deps for scan pipeline
            zbar
            tesseract
            poppler-utils

            # Playwright browsers (must match playwright version in pyproject.toml)
            # https://nixos.wiki/wiki/Playwright
            playwright-driver.browsers

            # For remarkable sync
            rmapi
          ];

          shellHook = ''
            export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
            export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
            export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE="ubuntu-24.04"
            echo "remarkable-gtd dev shell"
            uv --version
          '';
        };

        packages.default = pkgs.python3Packages.buildPythonApplication {
          pname = "remarkable-gtd";
          version = "0.1.0";
          format = "pyproject";

          src = ./.;

          nativeBuildInputs = with pkgs.python3Packages; [
            setuptools
            wheel
          ];

          propagatedBuildInputs = with pkgs.python3Packages; [
            numpy
            pillow
            rmscene
            playwright
            pypdf
            jinja2
            qrcode
            opencv-python
            pyzbar
            pytesseract
            pymupdf
            pytest
            pdf2image
          ];

          meta = with pkgs.lib; {
            description = "GTD paper workflow for reMarkable 2 — PDF generation + machine-vision scanner";
            license = licenses.mit;
          };
        };
      });
}
