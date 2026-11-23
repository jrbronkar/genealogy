{
  description = "Local genealogy atlas";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = fn: nixpkgs.lib.genAttrs systems (system: fn nixpkgs.legacyPackages.${system});
    in
    {
      apps = forAllSystems (pkgs:
        let
          atlas = pkgs.writeShellApplication {
            name = "genealogy-atlas";
            runtimeInputs = [ pkgs.python3 ];
            text = ''
              export PYTHONPATH=${./.}
              exec python3 -B -m storyboard.server "$@"
            '';
          };
        in
        {
          default = {
            type = "app";
            program = "${atlas}/bin/genealogy-atlas";
          };
          atlas = {
            type = "app";
            program = "${atlas}/bin/genealogy-atlas";
          };
        });
    };
}
