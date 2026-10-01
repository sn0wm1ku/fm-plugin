"""Trusted, read-only example tool with fabricated local catalog data."""

import json
import apple_fm_sdk as fm


@fm.generable("A local demonstration catalog lookup")
class LookupArguments:
    code: str = fm.guide("The catalog code to look up, for example FM-DEMO-7")


class LookupTool(fm.Tool):
    name = "lookup_catalog"
    description = "Look up a product's private demo catalog details by code."

    @property
    def arguments_schema(self):
        return LookupArguments.generation_schema()

    async def call(self, args):
        code = args.value(str, for_property="code")
        return json.dumps({
            "FM-DEMO-7": {"code": "FM-DEMO-7", "name": "Amber notebook", "stock": 37},
        }.get(code, {"error": "Unknown demo catalog code", "code": code}))


def create_tools():
    return [LookupTool()]
