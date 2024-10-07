import ctypes
import numpy as np
from PIL import Image

from sdl2 import *

from cffi import FFI

ffi = FFI()
ffi.set_source("_test", """
#include <stdio.h>
#include <stdint.h>
#include <pthread.h>

#define TEMP_BUF_BITS 16
#define TEMP_BUF_PAGE (1<<(TEMP_BUF_BITS))
#define TEMP_BUF_SIZE (TEMP_BUF_PAGE<<8)
#define TEMP_BUF_MASK (TEMP_BUF_SIZE-1)

uint8_t buffer[TEMP_BUF_SIZE];
uint8_t buffer_head = 0;
uint8_t buffer_tail = 0;

#define LPF_BITS 9
#define LPF_WINDOW (1 << LPF_BITS)
uint8_t buffer_lpf[TEMP_BUF_SIZE];
uint32_t lpf_acc = 0;
uint32_t lpf_pos = 0;

uint8_t running = 1;
pthread_t reader_thread;


void *buffer_thread(void *args) {
    int fd = *(int *)args;
    printf("fd = %d\\n", fd);
    memset(buffer, 0, TEMP_BUF_SIZE);
    while(running) {
        while (((buffer_head+1)&0xff) == buffer_tail) {
            usleep(1000);
            if (!running) {
                goto _exit;
            };
        }
        read(fd, buffer + (buffer_head << TEMP_BUF_BITS), TEMP_BUF_PAGE);
        uint32_t hist[256] = {0};
        for (int n = 0; n < TEMP_BUF_PAGE; n++) {
            lpf_acc += buffer[lpf_pos];
            lpf_acc -= buffer[(lpf_pos - LPF_WINDOW)&TEMP_BUF_MASK];
            buffer_lpf[(lpf_pos - (LPF_WINDOW>>1))&TEMP_BUF_MASK] = lpf_acc >> LPF_BITS;
            hist[lpf_acc >> LPF_BITS] += 1;
            lpf_pos = (lpf_pos + 1) & TEMP_BUF_MASK;
        }
        int a=-1, b=-1, c=0;
        for (int n=0; n<256; n++) {
            c+= hist[n];
            if (a == -1 && c > (TEMP_BUF_PAGE>>4)) {
                a = n;
            } else if (b == -1 && c > (TEMP_BUF_PAGE - (TEMP_BUF_PAGE>>4))) {
                b = n;
                break;
            }
        }
        int cutoff = (a + b) >> 1;
        buffer_head++;
    }
_exit:
    printf("exiting thread\\n");
    return 0;
}



int start_thread(int fd) {
    static int _fd = -1;
    _fd = fd;
    printf("fd = %d\\n", _fd);
    return pthread_create(&reader_thread, NULL, buffer_thread, &_fd);
}

void stop_thread() {
    running = 0;
    pthread_join(reader_thread, NULL);    
}


uint32_t page_pos = 0;
#define BUF_POS ((buffer_tail << TEMP_BUF_BITS) + page_pos)
#define AVAIL_PAGES ((buffer_head - buffer_tail - 1)&0xff)
#define AVAIL (((int)(AVAIL_PAGES << TEMP_BUF_BITS)) - (int)(LPF_WINDOW>>1) - (int)page_pos)
#define TO_END ((int)(TEMP_BUF_SIZE - BUF_POS))

void read_into(char *buf, int count) {
    //printf("%d\\n", AVAIL, count);
    while(AVAIL < count) {
        //printf("%d %d %d %d\\n", buffer_head, buffer_tail, AVAIL, count);
        usleep(1000);
    }
    int tx = 0;
    if (TO_END < count) {
        memcpy(buf, buffer + BUF_POS, TO_END);
        tx += TO_END;
        memcpy(buf + TO_END, buffer, count - TO_END);
        tx += count - TO_END;
        page_pos = count - TO_END;
    } else {
        memcpy(buf, buffer + BUF_POS, count);
        page_pos = BUF_POS + count;
        tx += count;
    }
    buffer_tail = page_pos >> TEMP_BUF_BITS;
    page_pos &= (1<<TEMP_BUF_BITS) - 1;
    //printf("%d, %d, %d\\n", tx, buffer_tail, page_pos);
}


""")
ffi.cdef("""
int start_thread(int);
void stop_thread();
void read_into(char *, uint32_t);
""")
ffi.compile(verbose=99)
from _test import lib  # import the compiled library



class Video:
    def __init__(self, cxadc, registers, sample_rate=28636363, refresh=25, lines=625, show_regs=True):
        self._cxadc = cxadc
        self._registers = registers
        self._lines = lines
        self._cmdthread = None
        self.show_regs = show_regs
        self._sample_rate = sample_rate
        self.set_standard(refresh, lines)
        self.screenshot = False
        self.pause = False

        self._screenw, self._screenh = 1680, 1050

        SDL_Init(SDL_INIT_VIDEO)
        self._window = SDL_CreateWindow(b"cxcvbs",
                                  SDL_WINDOWPOS_UNDEFINED,
                                  SDL_WINDOWPOS_UNDEFINED,
                                  1680, 1050, SDL_WINDOW_SHOWN)

        SDL_ShowCursor(False)
        self._renderer = SDL_CreateRenderer(self._window, -1, 0)
        self._texture = SDL_CreateTexture(self._renderer, SDL_PIXELFORMAT_RGB888, SDL_TEXTUREACCESS_STREAMING,
                                    2048, 1024)
        SDL_SetTextureScaleMode(self._texture, SDL_ScaleModeBest)
        self._palette = np.repeat(np.arange(256, dtype=np.uint8).reshape(-1, 1), 4, axis=1)

        self._palette[0] = (0, 0, 255, 255)
        for n in range(1, 64):
            self._palette[n] = (n*4, 0, 0, 255)
        self._palette[255] = (0, 255, 0, 255)

    def calculate_timings(self):
        self._samples_per_line = round(self._sample_rate / (self._refresh * self._lines))
        self._samples_per_frame = int(self._sample_rate / self._refresh)
        self._frac = (self._sample_rate / self._refresh) - self._samples_per_frame
        self._buffer = np.zeros((self._lines + 1, self._samples_per_line), dtype=np.uint8)
        self._buffer_p = ffi.cast("char *", ffi.from_buffer(self._buffer))

    def set_standard(self, refresh, lines):
        self._refresh = refresh
        self._lines = lines
        self.calculate_timings()

    def set_sample_rate(self, rate):
        self._sample_rate = rate
        self.calculate_timings()

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, rate):
        self._sample_rate = rate
        self.calculate_timings()

    def draw_bits(self, data, xpos, ypos):
        for y, i in enumerate(data):
            SDL_SetRenderDrawColor(self._renderer, 255, 255, 255, 255)
            for x in range(32):
                b = (i >> x) & 1
                xp = xpos + (x * 16) + ((x // 8) * 4)
                yp = ypos + (y * 16) + ((y // 4) * 4)
                (SDL_RenderFillRect if b else SDL_RenderDrawRect)(self._renderer, SDL_Rect(xp, yp, 14, 14))

    def draw_histogram(self, data, xpos=1144, ypos=1030):
        histogram = np.bincount(data)
        histogram = np.clip(histogram>>6, 0, 200)
        SDL_SetRenderDrawColor(self._renderer, 0, 0, 255, 255)
        SDL_RenderDrawLine(self._renderer, xpos + 128, ypos, xpos + 128, ypos - 200)
        SDL_SetRenderDrawColor(self._renderer, 255, 255, 255, 255)
        SDL_RenderDrawLine(self._renderer, xpos, ypos, xpos + 512, ypos)
        for n in range(histogram.shape[0]):
            SDL_RenderDrawLine(self._renderer, xpos + (n * 2), ypos, xpos + (n * 2),
                               ypos - histogram[n])


    def run(self):

        c = 0

        running = True
        event = SDL_Event()

        lib.start_thread(self._cxadc.fileno())

        while running:
            if self._cmdthread and not self._cmdthread.is_alive():
                self._cmdthread.join()
                break

            while SDL_PollEvent(ctypes.byref(event)) != 0:
                if event.type == SDL_QUIT:
                    running = False
                    break
                elif event.type == SDL_KEYUP:
                    print(event.key)

            c += self._frac
            extra = int(c)
            c -= extra
            lib.read_into(self._buffer_p, self._samples_per_frame + extra)

            if not self.pause:
                img = self._palette[self._buffer]

            SDL_SetRenderDrawColor(self._renderer, 0, 0, 0, 255)
            SDL_RenderClear(self._renderer)
            SDL_UpdateTexture(self._texture, SDL_Rect(0, 0, self._samples_per_line, self._lines), img.tobytes(), self._samples_per_line*4)
            SDL_RenderCopy(self._renderer, self._texture, SDL_Rect(0, 0, self._samples_per_line, self._lines), SDL_Rect(0, 0, self._screenw, self._lines if self.show_regs else self._screenh))

            if self.show_regs:
                self.draw_bits(self._registers.read_block(0x310100, 0x60), 20, 636)
                self.draw_bits(self._registers.read_block(0x310160, 0x4c), 20 + (1680//3), 636)
                self.draw_bits(self._registers.read_block(0x310200, 0x28), 20 + (2*1680//3), 636)

                self.draw_histogram(self._buffer.flatten()[:self._samples_per_frame + extra])

            SDL_RenderPresent(self._renderer)

            if self.screenshot:
                buffer = np.zeros((self._screenh, self._screenw, 4), dtype=np.uint8)
                SDL_RenderReadPixels(self._renderer, SDL_Rect(0, 0, self._screenw, self._screenh), SDL_PIXELFORMAT_ABGR8888, buffer.ctypes.data, self._screenw*4)
                Image.fromarray(buffer).save("screenshot.png")
                self.screenshot = False

        SDL_DestroyWindow(self._window)
        lib.stop_thread();
        return 0

