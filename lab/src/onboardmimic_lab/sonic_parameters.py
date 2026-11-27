"""Read numeric policy constants from the external SONIC header without eval."""
import ast
import math
from pathlib import Path
import re


def read_parameters(path):
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', Path(path).read_text(), flags=re.S)
    constants = {}

    def number(text):
        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return node.value
            if isinstance(node, ast.Name):
                return constants[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                a, b = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add): return a+b
                if isinstance(node.op, ast.Sub): return a-b
                if isinstance(node.op, ast.Mult): return a*b
                if isinstance(node.op, ast.Div): return a/b
            raise ValueError('unsupported SONIC numeric expression')
        result = visit(ast.parse(text.strip(), mode='eval').body)
        if not math.isfinite(result): raise ValueError('nonfinite SONIC constant')
        return result

    for name, expr in re.findall(r'const\s+(?:double|float|int)\s+(\w+)\s*=\s*([^;]+);', text):
        constants[name] = number(expr)
    arrays = {}
    for name in ('default_angles', 'g1_action_scale', 'kps', 'kds', 'isaaclab_to_mujoco', 'mujoco_to_isaaclab'):
        match = re.search(r'\b'+name+r'\s*=\s*\{([^}]+)\}', text)
        if not match: raise ValueError('SONIC parameter missing: '+name)
        values = [number(v) for v in match[1].split(',') if v.strip()]
        if len(values) != 29: raise ValueError('SONIC parameter must have 29 values: '+name)
        arrays[name] = values
    for name in ('isaaclab_to_mujoco', 'mujoco_to_isaaclab'):
        if sorted(arrays[name]) != list(range(29)): raise ValueError('invalid joint mapping')
        arrays[name] = [int(v) for v in arrays[name]]
    if any(arrays['isaaclab_to_mujoco'][arrays['mujoco_to_isaaclab'][i]] != i for i in range(29)):
        raise ValueError('joint mappings are not inverse')
    if any(v <= 0 for key in ('g1_action_scale', 'kps', 'kds') for v in arrays[key]):
        raise ValueError('invalid SONIC gains or scales')
    return arrays
