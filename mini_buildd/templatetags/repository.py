from django import template

register = template.Library()

@register.simple_tag
def repository_dist(repository, dist, suite):
    return repository.get_dist(dist, suite)

@register.simple_tag
def repository_origin(repository):
    return repository.get_origin()

@register.simple_tag
def repository_components(repository):
    return repository.get_components()

@register.simple_tag
def repository_archs(repository, sep=","):
    return sep.join(repository.get_archs())

@register.simple_tag
def repository_desc(repository, dist, suite):
    return repository.get_desc(dist, suite)

@register.simple_tag
def repository_apt_line(repository, dist, suite):
    return repository.get_apt_line(dist, suite)

@register.simple_tag
def repository_sources(repository, dist, suite):
    return repository.get_sources(dist, suite)

@register.simple_tag
def repository_mandatory_version(repository, dist, suite):
    return repository.get_mandatory_version(dist, suite)