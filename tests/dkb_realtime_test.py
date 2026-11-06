"""Differential verification for Bananza 1.0.0 realtime timing patches.

Tests all replacement sites lifted through recomp/lifter.py on synthetic CPU state,
verifying controller state updates, NVN present interval, and clock producer floating-point ratio loads.
"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'recomp'))
from lifter import Lifter

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc', default='clang')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/dkb-realtime-test')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    lifter = Lifter(0x7000000000, simd='c')
    patch_lines = (ROOT / 'recomp/patches-dkb100.txt').read_text().splitlines()
    
    bodies = []
    for line in patch_lines:
        tokens = line.split('#', 1)[0].split()
        if len(tokens) < 6 or tokens[:3] != ['site', 'realtime', 'main']:
            continue
        addr = int(tokens[3], 16)
        ops = []
        for word in tokens[5:]:
            if word.startswith('@page:x'):
                reg = int(word[7:])
                ops.append(f'c->x[{reg}] = (uintptr_t)page;')
            else:
                ins = lifter.lift(int(word, 16), 0x7000000000 + addr)
                assert ins.kind == 'op', f'{addr:#x}: expected op, got {ins.kind}'
                ops.append(ins.code)
        bodies.append(f'static void patch_{addr:x}(Cpu *c, void *page) {{\n    ' + '\n    '.join(ops) + '\n}')

    assert len(bodies) == 9, f'Expected 9 realtime patch sites, found {len(bodies)}'

    c_source = r'''#include "cpu.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

''' + '\n\n'.join(bodies) + r'''

int main(void) {
    unsigned total_cases = 0;
    unsigned failures = 0;

    for (unsigned hz = 15; hz <= 360; ++hz) {
        for (unsigned q = 0; q < 4; ++q) {
            float rate = (float)hz + (float)q * 0.25f;
            float ratio = 60.0f / rate;
            uint32_t rounded_fps = (uint32_t)lroundf(rate);

            _Alignas(16) unsigned char page[4096] = {0};
            _Alignas(16) unsigned char state[128];
            _Alignas(16) unsigned char expected_state[128];
            _Alignas(16) unsigned char stack[64];

            memcpy(page + 8, &ratio, 4);
            memcpy(page + 12, &rounded_fps, 4);

            memset(state, 0xaa, sizeof state);
            memcpy(expected_state, state, sizeof expected_state);

            Cpu c = {0};
            for (unsigned i = 0; i < 32; ++i) {
                c.x[i] = 0xdeadbeef00000000ull | (uint64_t)i;
                c.v[i] = ((__uint128_t)c.x[i] << 64) | ~c.x[i];
            }
            c.sp = (uintptr_t)(stack + sizeof stack);
            c.nzcv = 0x40000000;
            c.x[20] = (uintptr_t)state;

            // 1) Test controller common exit at 0x1ead24
            Cpu want = c;
            want.x[0] = c.x[19];
            want.x[1] = rounded_fps;
            want.x[8] = (uintptr_t)page;
            memcpy(expected_state + 0x50, &rounded_fps, 4);
            memcpy(expected_state + 0x54, &rounded_fps, 4);

            patch_1ead24(&c, page);
            if (memcmp(&c, &want, sizeof c) != 0 || memcmp(state, expected_state, sizeof state) != 0) {
                failures++;
            }

            // 2) Test present interval request at 0x1ead2c
            want = c;
            want.x[1] = 1;
            patch_1ead2c(&c, page);
            if (memcmp(&c, &want, sizeof c) != 0) {
                failures++;
            }

            // 3) Test all 7 clock producers
            void (*clocks[7])(Cpu*, void*) = {
                patch_1bc834, patch_2d9fc8, patch_2da120, patch_34e99c, patch_34ec1c, patch_34ec68, patch_69fc60
            };

            for (unsigned k = 0; k < 7; ++k) {
                memset(stack, 0x55, sizeof stack);
                c.x[8] = (uintptr_t)state;
                want = c;
                uint32_t ratio_bits;
                memcpy(&ratio_bits, &ratio, 4);
                want.v[k == 4 ? 8 : 0] = ratio_bits;

                clocks[k](&c, page);
                if (memcmp(&c, &want, sizeof c) != 0 || memcmp(state, expected_state, sizeof state) != 0) {
                    failures++;
                }
                for (size_t s = 0; s < sizeof stack - 16; ++s) {
                    if (stack[s] != 0x55) {
                        failures++;
                        break;
                    }
                }
                total_cases++;
            }
        }
    }

    printf("Bananza realtime timing check: %u cases, %u failures\n", total_cases, failures);
    return failures != 0;
}
'''
    out_c = args.out / 'runner.c'
    out_c.write_text(c_source)

    for opt in ('-O0', '-O2'):
        bin_name = 'runner_' + opt.lstrip('-') + ('.exe' if sys.platform == 'win32' else '')
        out_bin = args.out / bin_name
        subprocess.run([
            args.cc, opt, '-march=x86-64-v3', '-fno-strict-aliasing',
            '-I', str(ROOT / 'runtime'), str(out_c), '-o', str(out_bin)
        ], check=True)
        subprocess.run([str(out_bin)], check=True)

if __name__ == '__main__':
    main()
