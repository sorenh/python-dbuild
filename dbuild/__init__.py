#!/usr/bin/env python
import argparse
import sys
import os
import shutil
import glob
from dbuild import exceptions

from jinja2 import Environment, FileSystemLoader
from docker import Client
from tempfile import mkdtemp


def docker_client(url='unix://var/run/docker.sock'):
    """ return docker client """
    return Client(url)


def build_image(docker_client, path, tag):
    """ Build docker image"""
    return '\n'.join((
        ''.join(line.values()).strip() for line in docker_client.build(
            path=path, rm=True, forcerm=True, tag=tag, decode=True)))
    #return ''.join(['\n'.join(line.values()) for line in docker_client.build(
    #    path=path, rm=True, forcerm=True, tag=tag, decode=True)])


def create_container(docker_client, image, name=None, command=None, env=None,
                     disable_network=False, shared_volumes=None, cwd=None):
    """ create docker containers """
    if shared_volumes:
        volumes = shared_volumes.values()
        binds = ['{}:{}'.format(k, v) for k, v in shared_volumes.iteritems()]
        host_config = docker_client.create_host_config(binds=binds)
    else:
        host_config = None

    container = docker_client.create_container(
        image=image, name=name, command=command, environment=env,
        network_disabled=disable_network, volumes=volumes,
        working_dir=cwd, host_config=host_config)
    return container


def start_container(docker_client, container):
    """ Start docker container """
    response = docker_client.start(container=container.get('Id'))
    return response


def wait_container(docker_client, container):
    """ Wait  for the container to finish execution """
    rv = docker_client.wait(container=container)
    return rv


def container_logs(docker_client, container):
    """ Get container stdout and stderr """
    return [log.strip() for log in docker_client.logs(container=container,
                                                      stream=True,
                                                      timestamps=True)]


def remove_container(docker_client, container, force=False):
    """ Remove docker container """
    return docker_client.remove_container(container=container, force=force)


def create_docker_dir(flavor, dist):
    PATH = os.path.dirname(os.path.abspath(__file__))
    TMPL_ENV = Environment(
        autoescape=False,
        loader=FileSystemLoader(os.path.join(PATH, 'templates')),
        trim_blocks=False)

    # Create docker_dir - a temporary directory which will have Dockerfile and
    # scripts to build the container.
    docker_dir = mkdtemp()
    dockerfile = os.path.join(docker_dir, 'Dockerfile')
    ctxt = {'flavor': flavor, 'dist': dist,
            'maintainer': 'dbuild, dbuild@test.com', }

    # Write Dockerfile under docker_dir
    with open(dockerfile, 'w') as d:
        dockerdata = TMPL_ENV.get_template('dockerfile.jinja').render(ctxt)
        d.write(dockerdata)

    # Copy scripts under docker_dir
    shutil.copytree(os.path.join(PATH, 'scripts'),
                    os.path.join(docker_dir, 'scripts'))
    return docker_dir


def docker_build(build_dir, build_type, source_dir='source', force_rm=False,
                 docker_url='unix://var/run/docker.sock', flavor='ubuntu',
                 dist='trusty'):
    c = docker_client(docker_url)

    docker_path = create_docker_dir(flavor, dist)

    print "Starting %s Package Build" % build_type
    image_tag = 'dbuild-' + flavor + '/' + dist
    response = build_image(c, docker_path, tag=image_tag)
    print response

    if build_type == 'source':
        command = ['dpkg-buildpackage', '-S', '-nc', '-uc', '-us']
        cwd = '/build/' + source_dir
    elif build_type == 'binary':
        command = ['bash', '-c',
                   "dpkg-source -x /build/*.dsc /build/pkgbuild/ && \
                   cd /build/pkgbuild && \
                   /usr/lib/pbuilder/pbuilder-satisfydepends && \
                   dpkg-buildpackage"]
        cwd = '/build'
    else:
        shutil.rmtree(docker_path)
        raise exceptions.DbuildBuildFailedException(
            'Unknown build_type: %s' % build_type)

    container = create_container(c, image_tag, command=command, cwd=cwd,
                                 shared_volumes={build_dir: '/build'})
    print(container)
    response = start_container(c, container)
    rv = wait_container(c, container)
    logs = container_logs(c, container)
    print '\n'.join(logs)

    if rv == 0:
        print 'Build successful (build type: %s), removing container %s' % (
            build_type, container.get('Id'))
        remove_container(c, container, force=True)
        build_rv = True
    else:
        if force_rm:
            print "Build failed (build type: %s), Removing container %s" % (
                build_type, container.get('Id'))
            remove_container(c, container, force=True)
            build_rv = False
        else:
            print "Build failed (build type: %s), keeping container %s" % (
                build_type, container.get('Id'))
            build_rv = False

    shutil.rmtree(docker_path)
    if build_rv:
        return build_rv
    elif build_type == 'source':
        raise exceptions.DbuildSourceBuildFailedException(
            'Source build FAILED')
    elif build_type == 'binary':
        raise exceptions.DbuildBinaryBuildFailedException(
            'Binary build FAILED')


def main(argv=sys.argv):
    ap = argparse.ArgumentParser(
        description='Build debian packages in docker container')
    ap.add_argument('--build_dir', type=str, help='package build directory')
    ap.add_argument('--source_dir', type=str, default='source',
                    help='subdirectory of build_dir where sources kept')
    ap.add_argument('--force_rm', action='store_true', default=False,
                    help='Remove the containers even if build failed')
    ap.add_argument('--docker_url', type=str,
                    default='unix://var/run/docker.sock',
                    help='Docker url, it can be unix socket or tcp url')
    ap.add_argument('--flavor', type=str, default='ubuntu',
                    help='Linux flavor to use for container to build')
    ap.add_argument('--dist', type=str, default='trusty',
                    help='Linux distribution')

    args = ap.parse_args()

    if (not args.build_dir):
        print 'build_dir must be provided'
        ap.print_help()
        sys.exit(1)

    try:
        docker_build(build_dir=args.build_dir,
                     build_type='source', source_dir=args.source_dir,
                     force_rm=args.force_rm, docker_url=args.docker_url,
                     flavor=args.flavor, dist=args.dist)
    except exceptions.DbuildSourceBuildFailedException:
        print 'ERROR | Source build failed for build directory: %s' % args.build_dir
        return False

    try:
        docker_build(build_dir=args.build_dir,
                     build_type='binary', source_dir=args.source_dir,
                     force_rm=args.force_rm, docker_url=args.docker_url,
                     flavor=args.flavor, dist=args.dist)
    except exceptions.DbuildBinaryBuildFailedException:
        print 'ERROR | Binary build failed for build directory: %s' % args.build_dir
        return False

    return True

if __name__ == "__main__":
    sys.exit(not main(sys.argv))
