import urllib.parse


def _inspect_distribution(api, image_name):
    # podman-py's ImagesManager.get_registry_data() only reads local images (it wraps
    # GET /images/{name}/json, which requires the image to already exist), so it cannot
    # be used to check remote availability before a pull. Hit the Docker-compat endpoint
    # directly instead (same one docker-py's inspect_distribution() uses).
    name = urllib.parse.quote_plus(image_name)
    resp = api.get(f"/distribution/{name}/json", compatible=True)
    resp.raise_for_status()
    return resp.json()
