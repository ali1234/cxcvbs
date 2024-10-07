import click
import os
import pathlib

from . import memory
from . import video
from . import command


@click.command()
@click.option('-d', '--device', type=click.Path(readable=True), default='/dev/swradio0')
@click.option('--video/--no-video', 'show_video', default=True, help='Show video on screen')
@click.option('--regs/--no-regs', 'show_regs', default=True, help='Show registers on video')
@click.option('-x', '--xtal', type=int, default=28636383)
@click.option('-s', '--standard', type=click.Choice(('PAL', 'NTSC')), default='PAL')
def main(device, show_video, show_regs, xtal, standard):
    size = 0x400000
    rdev = os.stat(device).st_rdev
    major = rdev >> 8
    minor = rdev & 0xff
    pcires = pathlib.Path('/sys/dev/char/') / f'{major}:{minor}' / 'device/resource0'

    with memory.Memory(pcires, size) as mem:
        print(pcires, 'opened')
        if show_video:
            dev = open(device, 'rb')
            print(device, 'opened')
            if standard == 'PAL':
                vid = video.Video(dev, mem, sample_rate=xtal, refresh=25, lines=625, show_regs=show_regs)
            else:
                vid = video.Video(dev, mem, sample_rate=xtal, refresh=29.97, lines=525, show_regs=show_regs)
        else:
            vid = None
        th = command.run_thread(mem, vid)
        if vid:
            vid._cmdthread = th
            vid.run()
        else:
            th.join()

    print("Exiting")


if __name__ == '__main__':
    main()
