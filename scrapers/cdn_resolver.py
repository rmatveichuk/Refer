"""CDN URL normalization without network requests or external dependencies.

The returned URL is a candidate for the original/highest-resolution asset.
Availability and actual image dimensions must be checked by the caller.
"""

import re
from urllib.parse import unquote_plus, urlsplit, urlunsplit


_ARCHDAILY_VARIANT_RE = re.compile(
    r"/(?:medium_jpg|large_jpg|slideshow|thumb_jpg|newsletter)(?=/)"
)

_WEBFLOW_VARIANT_RE = re.compile(
    r"-p-(?:500|800|1080|1600)(?=\.[a-z0-9]+$)",
    re.IGNORECASE,
)

_CLOUDINARY_VERSION_RE = re.compile(r"v\d+")
_CLOUDINARY_SIGNATURE_RE = re.compile(r"s--[^/]+--")

# Recognized Cloudinary transformation keys.
# Explicit matching avoids treating every path segment with "_" as a transform.
_CLOUDINARY_OPTION_RE = re.compile(
    r"(?:"
    r"a|ac|af|ar|b|bo|br|c|co|cs|d|dl|dn|dpr|du|"
    r"e|eo|f|fl|fn|fps|g|h|if|ki|l|o|p|pg|q|r|"
    r"so|sp|t|u|vc|vs|w|x|y|z|zoom"
    r")_.+"
)

_IMGIX_REMOVE_KEYS = frozenset({"w", "h", "fit", "crop", "q"})


def _is_host(host: str, domain: str) -> bool:
    """Match a domain or its subdomains, but not look-alike suffixes."""
    return host == domain or host.endswith("." + domain)


def _query_key(parameter: str) -> str:
    """Decode only a query key, leaving parameter values untouched."""
    return unquote_plus(parameter.partition("=")[0])


def _remove_query_keys(query: str, keys: frozenset) -> str:
    """Remove selected parameters without re-encoding retained parameters.

    Preserves the spelling, encoding and order of retained security tokens.
    This does not guarantee that their signatures remain valid.
    """
    return "&".join(
        parameter
        for parameter in query.split("&")
        if _query_key(parameter) not in keys
    )


def _set_query_parameter(query: str, key: str, value: str) -> str:
    """Replace a parameter and collapse duplicates without re-encoding others."""
    parameters = []
    replaced = False

    for parameter in query.split("&") if query else []:
        if _query_key(parameter) == key:
            if not replaced:
                parameters.append(f"{key}={value}")
                replaced = True
        else:
            parameters.append(parameter)

    if not replaced:
        parameters.append(f"{key}={value}")

    return "&".join(parameters)


def _is_cloudinary_transform(segment: str) -> bool:
    """Recognize comma-separated options in a transformation path segment."""
    return bool(segment) and all(
        _CLOUDINARY_OPTION_RE.fullmatch(option)
        for option in segment.split(",")
    )


def _resolve_cloudinary_path(path: str) -> str:
    """Strip leading transformation segments from image/upload URLs.

    Stops at a version segment or the first unrecognized public-ID segment.
    The version and all remaining public-ID folders are preserved.

    Signed URL signatures are retained, but changing the transformation can
    invalidate them. Unversioned public IDs that resemble transformations
    are inherently ambiguous.
    """
    parts = path.split("/")

    # Expected structure: /<cloud_name>/image/upload/<...>
    if (
        len(parts) < 5
        or not parts[1]
        or parts[2:4] != ["image", "upload"]
    ):
        return path

    start = 4

    # A signature, when present, precedes the transformation chain.
    if (
        start < len(parts)
        and _CLOUDINARY_SIGNATURE_RE.fullmatch(parts[start])
    ):
        start += 1

    end = start

    # Always leave at least one segment for the public ID.
    while end < len(parts) - 1:
        segment = parts[end]

        if _CLOUDINARY_VERSION_RE.fullmatch(segment):
            break

        if not _is_cloudinary_transform(segment):
            break

        end += 1

    if end == start:
        return path

    return "/".join(parts[:start] + parts[end:])


def resolve_master_url(url: str) -> str:
    """Return a best-effort original/high-resolution image URL.

    Supported providers:
      * ArchDaily
      * Cloudinary image/upload
      * Imgix, including custom domains with both w and fit parameters
      * Squarespace
      * Webflow

    Unknown providers and unsupported URL structures remain unchanged.
    No requests, downloads or availability checks are performed.
    """
    if not isinstance(url, str):
        raise TypeError("url must be a string")

    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return url

    # Also allow protocol-relative URLs: //example.com/image.jpg.
    if not host or parsed.scheme.lower() not in ("", "http", "https"):
        return url

    path = parsed.path
    query = parsed.query

    if host == "images.adsttc.com":
        path = _ARCHDAILY_VARIANT_RE.sub("/large_jpg", path)
        query = ""

    elif host == "res.cloudinary.com":
        path = _resolve_cloudinary_path(path)

    elif host == "images.squarespace-cdn.com":
        query = _set_query_parameter(query, "format", "original")

    elif _is_host(host, "website-files.com"):
        path = _WEBFLOW_VARIANT_RE.sub("", path)

    else:
        query_keys = {
            _query_key(parameter)
            for parameter in query.split("&")
            if parameter
        }

        is_imgix = _is_host(host, "imgix.net")
        looks_like_imgix = {"w", "fit"}.issubset(query_keys)

        if not (is_imgix or looks_like_imgix):
            return url

        query = _remove_query_keys(query, _IMGIX_REMOVE_KEYS)

    # Preserve the input verbatim when no transformation was necessary.
    if path == parsed.path and query == parsed.query:
        return url

    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, query, parsed.fragment)
    )


if __name__ == "__main__":
    cases = [
        (
            "ArchDaily",
            "https://images.adsttc.com/media/images/abc/medium_jpg/house.jpg"
            "?w=1200&q=75",
            "https://images.adsttc.com/media/images/abc/large_jpg/house.jpg",
        ),
        (
            "Cloudinary",
            "https://res.cloudinary.com/demo/image/upload/"
            "c_fill,w_1200,h_800/q_auto,f_auto,dpr_2/"
            "v1700000000/projects/house.jpg",
            "https://res.cloudinary.com/demo/image/upload/"
            "v1700000000/projects/house.jpg",
        ),
        (
            "Imgix",
            "https://studio.imgix.net/projects/house.jpg"
            "?w=1200&h=800&fit=crop&crop=faces&q=75"
            "&s=abc%2F123&expires=1900000000&signature=xyz",
            "https://studio.imgix.net/projects/house.jpg"
            "?s=abc%2F123&expires=1900000000&signature=xyz",
        ),
        (
            "Squarespace",
            "https://images.squarespace-cdn.com/content/v1/site/house.jpg"
            "?format=750w&token=abc",
            "https://images.squarespace-cdn.com/content/v1/site/house.jpg"
            "?format=original&token=abc",
        ),
        (
            "Webflow",
            "https://cdn.prod.website-files.com/site/house-p-1080.jpg"
            "?v=2",
            "https://cdn.prod.website-files.com/site/house.jpg?v=2",
        ),
    ]

    for name, source, expected in cases:
        actual = resolve_master_url(source)

        assert actual == expected, (
            f"{name} failed:\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}"
        )

        # Repeated normalization should not change the result.
        assert resolve_master_url(actual) == actual, (
            f"{name}: normalization is not idempotent"
        )

        print(f"[OK] {name}: {actual}")

    print(f"\nAll {len(cases)} CDN tests passed.")
