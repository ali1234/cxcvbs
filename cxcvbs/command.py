import ast
import base64
import binascii
import cmd
import shlex
import struct
import threading
import zlib


class Command(cmd.Cmd):
    intro = '\ncxcvbs debugger. Type help or ? to list commands.'
    prompt = 'cxcvbs> '

    init = [
        #"set pll_adj_en 0",
        #"set verten 1",
        #"set htotal 0x7ff",
        #"mux 2",
        #"import eNpjYTRkWMHAwNDAQBcgCKGYGDga7OGCbjCGABASAkyGDBpYhDn+e8jB2AEaQBX/IUYBAOZ/BhY=",
        #"mux 2",
        #"pal",
    ]

    def __init__(self, memory, video=None, init_cmd=None):
        super().__init__()
        self._memory = memory
        self._video = video

        if init_cmd is None:
            init_cmd = self.init

        for command in init_cmd:
            print(self.prompt + command)
            self.onecmd(command)

    def do_hd(self, arg):
        """Dump all registers as hex"""
        for addr, size in ((0x310100, 0xac), (0x310200, 0x28)):
            data = self._memory.read_block(addr, size)
            for n in range(0, size, 0x20):
                print(f"{addr+n:06x}:", *(f"{x:08x}" for x in data[n>>2:(n>>2)+0x8]))

    def do_export(self, arg):
        """Dump all registers as base64"""
        blobs = []
        for addr, size in ((0x310104, 0xa8), (0x310200, 0x28)):
            data = self._memory.read_block(addr, size)
            blobs.append(struct.pack('<II', addr, size))
            blobs.append(struct.pack(f'<{size>>2}I', *data))
        blob = base64.b64encode(zlib.compress(b''.join(blobs), level=9))
        print("To restore current settings run:")
        print("    import", blob.decode('ascii'))

    def do_import(self, arg):
        """Import base64 registers"""
        try:
            all_data = zlib.decompress(base64.b64decode(arg, validate=True))
        except (zlib.error, binascii.Error):
            print("Corrupted import string")
            return
        else:
            pos = 0
            while pos < len(all_data):
                addr, size = struct.unpack('<II', all_data[pos:pos + 8])
                pos += 8
                data = struct.unpack(f'<{size >> 2}I', all_data[pos:pos + size])
                for n in range(0, size, 4):
                    self._memory.write_word(addr+n, data[n>>2], mask=0xffffffff)
                pos += size
            self.onecmd("hd")

    def do_describe(self, arg):
        """Describe a register or address"""
        try:
            print(self._memory.find(arg).description)
        except KeyError:
            print("Unknown register or address")

    def do_get(self, arg):
        """Get value in a register or memory address"""
        try:
            obj = self._memory.find(arg)
            for k, v in obj.value.items():
                print(f'{k}: {v} (0x{v:x})')
        except KeyError:
            print("Unknown register or address")

    def do_set(self, arg):
        """Set value in a register or memory address"""
        args = shlex.split(arg)
        try:
            value = ast.literal_eval(args[1])
            if not isinstance(value, int):
                raise ValueError
            obj = self._memory.find(args[0])
            obj.value = value
            self.onecmd(f'get {args[0]}')
        except KeyError:
            print("Unknown register or address")
        except ValueError:
            print("Could not parse value")

    def do_mux(self, arg):
        """Set the input mux (YADC_SEL)"""
        self.onecmd(f'set yadc_sel {arg}')

    def do_pal(self, arg):
        """Set video window timings for PAL"""
        if self._video:
            self._video.set_standard(25, 625)

    def do_ntsc(self, arg):
        """Set video window timings for NTSC"""
        if self._video:
            self._video.set_standard(29.97, 525)

    def do_screenshot(self, arg):
        if self._video:
            self._video.screenshot = True

    def do_exit(self, arg):
        """Exit the program"""
        return True


def run_thread(memory, video, init_cmd=None):
    c = Command(memory, video, init_cmd)
    ct = threading.Thread(target=c.cmdloop, daemon=True)
    ct.start()
    return ct
