#python-dbuild
python-dbuild is a tool that can be used to build Debian packages using docker
containers. This will improve the security compared with sbuild on package builds
by building the packages in separate containers.

This code is mostly a rewrite in python of
https://github.com/shadeslayer/dbuild, which is written in ruby.
