# Kernel module for RAM lights on Batocera

`<kernel version>/acpi_call.ko` is the `acpi_call` module (GPL-2.0-or-later,
source: the `acpi-call-dkms` package, https://github.com/nix-community/acpi_call)
built for that Batocera kernel. It lets the service ask the BIOS to set the
RAM lights. Batocera does not ship it and has no compiler, so it is built on
another PC and loaded by the OMEN_LIGHTS service.

It only fits the exact kernel it was built for. `6.18.16` is Batocera 43.1.
After a Batocera update with a new kernel the RAM lights stop (everything
else keeps working) until a module for the new kernel is added here.

How it was built:

1. Kernel source of the same version from kernel.org, with Batocera's
   configuration (`board/batocera/x86/linux_x86_64-defconfig.config` from
   the matching tag of batocera.linux): `make olddefconfig modules_prepare`.
2. Batocera's kernel checks a checksum for every symbol a module uses.
   Those were read from Batocera's own modules with
   `modprobe --dump-modversions` and written to `Module.symvers`.
3. `make -C <kernel source> M=<acpi_call source> modules`.
