#!/usr/bin/env python
import argparse
import sys
import os
import shutil
from dbuild import exceptions

from jinja2 import Environment, FileSystemLoader
from docker import Client
from tempfile import mkdtemp


def docker_client(url='unix://var/run/docker.sock'):
    """ return docker client """
    return Client(url)


def build_image(docker_client, path, tag, nocache=False):
    """ Build docker image"""
    lines = [line.values()[0] for line in docker_client.build(
        path=path, rm=True, forcerm=True, tag=tag, decode=True, nocache=nocache)]
    if any(isinstance(x, dict) for x in lines):
        message = ''
        for l in lines:
            if isinstance(l, dict):
                error = ''.join(l.values())
                message += error
            else:
                message += l

        raise exceptions.DbuildDockerBuildFailedException(
            '''Docker build failed
Error message: %s

Full build messages

%s''' % (error, message))
    else:
        return ''.join(lines)


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


def create_docker_dir(dist, release):
    """Create docker directory and populate it"""
    PATH = os.path.dirname(os.path.abspath(__file__))
    TMPL_ENV = Environment(
        autoescape=False,
        loader=FileSystemLoader(os.path.join(PATH, 'templates')),
        trim_blocks=False)

    # Create docker_dir - a temporary directory which will have Dockerfile and
    # scripts to build the container.
    docker_dir = mkdtemp()
    dockerfile = os.path.join(docker_dir, 'Dockerfile')

    ctxt = {'dist': dist, 'release': release,
            'maintainer': 'dbuild, dbuild@test.com'}

    # Write Dockerfile under docker_dir
    with open(dockerfile, 'w') as d:
        dockerdata = TMPL_ENV.get_template('dockerfile.jinja').render(ctxt)
        d.write(dockerdata)

    # Copy scripts under docker_dir
    shutil.copytree(os.path.join(PATH, 'scripts'),
                    os.path.join(docker_dir, 'scripts'))
    return docker_dir

def docker_build(build_dir, build_type, source_dir='source', force_rm=False,
                 docker_url='unix://var/run/docker.sock', dist='ubuntu',
                 release='trusty', extra_repos_file='repos',
                 extra_repo_keys_file='keys', build_cache=True):
    c = docker_client(docker_url)

    docker_path = create_docker_dir(dist, release)

    print "Starting %s Package Build" % build_type
    image_tag = 'dbuild-' + dist + '/' + release
    response = build_image(c, docker_path, tag=image_tag, nocache=not build_cache)
    print response
    command=['bash', '-c', '']

    if os.path.exists(os.path.join(build_dir, extra_repos_file)):
        command[2] += 'cp /build/%s \
        /etc/apt/sources.list.d/dbuild-extra-repos.list && ' % extra_repos_file
    else:
        raise DbuildBuildFailedException('wrong extra repos file')

    if os.path.exists(os.path.join(build_dir, extra_repo_keys_file)):
        command[2] += 'apt-key add /build/%s && ' % extra_repo_keys_file
    else:
        raise DbuildBuildFailedException('wrong extra repo keys file')

    command[2] += 'export DEBIAN_FRONTEND=noninteractive; apt-get -y update \
                   && apt-get -y dist-upgrade && '

    if build_type == 'source':
        command[2] += 'dpkg-buildpackage -S -nc -uc -us'
        cwd = '/build/' + source_dir
    elif build_type == 'binary':
        command[2] += "dpkg-source -x /build/*.dsc /build/pkgbuild/ && \
                      cd /build/pkgbuild && \
                      /usr/lib/pbuilder/pbuilder-satisfydepends && \
                      dpkg-buildpackage"
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
    ap.add_argument('--build-dir', type=str, help='package build directory')
    ap.add_argument('--source-dir', type=str, default='source',
                    help='subdirectory of build_dir where sources kept')
    ap.add_argument('--force-rm', action='store_true', default=False,
                    help='Remove the containers even if build failed')
    ap.add_argument('--docker-url', type=str,
                    default='unix://var/run/docker.sock',
                    help='Docker url, it can be unix socket or tcp url')
    ap.add_argument('--dist', type=str, default='ubuntu',
                    help='Linux dist to use for container to build')
    ap.add_argument('--release', type=str, default='trusty',
                    help='Linux release name')
    ap.add_argument('--extra-repos-file', type=str, default='repos',
                    help='Relative file path from build-dir which contain \
                          apt source specs which is suitable for apt \
                          sources.list file.')
    ap.add_argument('--extra-repo-keys-file', type=str, default='keys',
                    help='relative file path from build-dir which contain \
                    all keys for any extra repos.')
    ap.add_argument('--build-cache', action='store_false', default=True,
                    help='Whether to use docker build cache or not')

    args = ap.parse_args()

    if (not args.build_dir):
        print 'build_dir must be provided'
        ap.print_help()
        sys.exit(1)

    try:
        docker_build(build_dir=args.build_dir,
                     build_type='source', source_dir=args.source_dir,
                     force_rm=args.force_rm, docker_url=args.docker_url,
                     dist=args.dist, release=args.release,
                     extra_repos_file=args.extra_repos_file,
                     extra_repo_keys_file=args.extra_repo_keys_file,
                     build_cache=args.build_cache)
    except exceptions.DbuildSourceBuildFailedException:
        print 'ERROR | Source build failed for build directory: %s' \
            % args.build_dir
        return False

    try:
        docker_build(build_dir=args.build_dir,
                     build_type='binary', source_dir=args.source_dir,
                     force_rm=args.force_rm, docker_url=args.docker_url,
                     dist=args.dist, release=args.release,
                     extra_repos_file=args.extra_repos_file,
                     extra_repo_keys_file=args.extra_repo_keys_file,
                     build_cache=args.build_cache)
    except exceptions.DbuildBinaryBuildFailedException:
        print 'ERROR | Binary build failed for build directory: %s' \
            % args.build_dir
        return False

    return True

if __name__ == "__main__":
    sys.exit(not main(sys.argv))
