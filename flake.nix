{
  description = "Anthropic-Teach - project-local Python env (uv-managed), isolated from global home.nix/configuration.nix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          # Toolchain layer only: interpreter, uv, git. Project libraries are
          # NEVER Nix packages here - they belong to pyproject.toml + uv.lock.
          packages = [
            pkgs.python312
            pkgs.uv
            pkgs.git
            # ruff is a tool, not a project library, so it belongs in this layer.
            # It is also the only workable home for it: the PyPI wheel ships a
            # dynamically linked binary that cannot run on NixOS.
            pkgs.ruff
          ];

          shellHook = ''
            # Insurance for prebuilt wheels that dlopen libstdc++/libz at runtime.
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.zlib}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            # Pin uv to the Nix interpreter so it never silently downloads its own.
            # Must stay in step with requires-python in pyproject.toml and .python-version.
            export UV_PYTHON="${pkgs.python312}/bin/python3"

            # --frozen so lock drift fails loudly instead of rewriting uv.lock
            # as a side effect of entering the shell.
            echo "Syncing .venv from uv.lock (frozen)..."
            uv sync --frozen

            # LD_LIBRARY_PATH above only covers processes this shell starts.
            # A Jupyter kernel is usually NOT one of them: VS Code, PyCharm and
            # `jupyter lab` launched from a desktop session all exec
            # .venv/bin/python directly, with none of the shell's environment.
            # pyzmq's wheel then fails to dlopen libstdc++.so.6 and the kernel
            # dies with "Kernel died before replying to kernel_info" - which is
            # what the editor reports as "Select an Interpreter to start Jupyter".
            #
            # A .pth file fixes it for every launcher instead of just this shell:
            # `site` executes it at interpreter startup, before any import, so
            # the libraries are already resident by the time pyzmq looks. The
            # store paths are interpolated by Nix, so this stays pinned to the
            # same nixpkgs revision as the interpreter and is regenerated on
            # every `nix develop` - including the first one after a fresh clone.
            cat > .venv/lib/python3.12/site-packages/zz-nixos-preload.pth <<'PTH'
            import ctypes; [ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL) for p in ("@STDCXX@", "@LIBZ@")]
            PTH
            # The heredoc is indented for readability; .pth lines must not be.
            sed -i \
              -e 's/^ *//' \
              -e 's|@STDCXX@|${pkgs.stdenv.cc.cc.lib}/lib/libstdc++.so.6|' \
              -e 's|@LIBZ@|${pkgs.zlib}/lib/libz.so.1|' \
              .venv/lib/python3.12/site-packages/zz-nixos-preload.pth

            echo "Anthropic-Teach dev shell ready (uv-managed, project-local)."
            echo "Python: $(python3 --version 2>/dev/null || echo 'not on PATH until venv is activated')"
            echo "Activate: source .venv/bin/activate"
          '';
        };
      });
}
