"""Hidden screen routes, mounted only in the multi-repository dashboard."""

from starlette.routing import BaseRoute, Route

from aifactory.web.screen import screen_get, screen_input, screen_stream

ROUTES: list[BaseRoute] = [
    Route("/screen/input", screen_input, methods=["POST"]),
    Route("/screen", screen_get, methods=["GET"]),
    Route("/screen/stream", screen_stream, methods=["GET"]),
]
