from app.services.structured_output_parser import StructuredOutputParser


def parse_json_response(response: str):
    return StructuredOutputParser.parse(response).data
