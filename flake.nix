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

        pythonPackages = pkgs.python3.withPackages (ps: with ps; [
          # Base
          numpy
          pillow

          # gen extras
          playwright
          pypdf
          jinja2
          qrcode

          # scan extras
          opencv-python
          pyzbar
          pytesseract
          pymupdf

          # dev extras
          pytest
          pdf2image
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          name = "remarkable-gtd";

          buildInputs = with pkgs; [
            pythonPackages
            pip

            # System deps for scan pipeline
            zbar
            tesseract
            poppler_utils

            # For playwright browser install
            playwright-driver.browsers

            # For remarkable sync
            rmapi
          ];

          shellHook = ''
            export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
            export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
            echo "remarkable-gtd dev shell"
            python --version
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
            playwright
            pypdf
            jinja2
            qrcode
            opencv-python
            pyzbar
            pytesseract
            pymupdf
          ];

          meta = with pkgs.lib; {
            description = "GTD paper workflow for reMarkable 2 — PDF generation + machine-vision scanner";
            license = licenses.mit;
          };
        };
      });
}
