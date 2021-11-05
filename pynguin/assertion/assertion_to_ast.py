#  This file is part of Pynguin.
#
#  SPDX-FileCopyrightText: 2019–2021 Pynguin Contributors
#
#  SPDX-License-Identifier: LGPL-3.0-or-later
#
"""Provides an assertion visitor to transform assertions to AST."""
import array
import ast
from _ast import Attribute, Constant, Name
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

import pynguin.assertion.assertionvisitor as av
import pynguin.assertion.noneassertion as na
import pynguin.assertion.primitiveassertion as pa
import pynguin.configuration as config
import pynguin.testcase.variablereference as vr
import pynguin.utils.ast_util as au
import pynguin.utils.namingscope as ns
import pynguin.utils.type_utils as tu


class AssertionToAstVisitor(av.AssertionVisitor):
    """An assertion visitor that transforms assertions into AST nodes."""

    COMPARISON_OBJECT_IDENTIFIER: str = "obj"
    _obj_index: int = 0

    def __init__(
        self,
        variable_names: ns.AbstractNamingScope,
        module_aliases: ns.AbstractNamingScope,
        common_modules: Set[str],
    ):
        """Create a new assertion visitor.

        Args:
            variable_names: the naming scope that is used to resolve the names
                            of the variables used in the assertions.
            module_aliases: the naming scope that is used to resolve the aliases of the
                            modules used in the assertions.
            common_modules: the set of common modules that are used. Modules may be
                            added when transforming the assertions.
        """
        self._common_modules = common_modules
        self._module_aliases = module_aliases
        self._variable_names = variable_names
        self._nodes: List[ast.stmt] = []
        self._obj_stack: List[str] = []

    @property
    def nodes(self) -> List[ast.stmt]:
        """Provides the ast nodes generated by this visitor.

        Returns:
            the ast nodes generated by this visitor.
        """
        return self._nodes

    def visit_primitive_assertion(self, assertion: pa.PrimitiveAssertion) -> None:
        """
        Creates an assertion of form "assert var0 == value" or assert var0 is False,
        if the value is a bool.

        Args:
            assertion: the assertion that is visited.

        """
        if isinstance(assertion.value, bool):
            self._nodes.append(
                self._create_constant_assert(
                    assertion.source, ast.Is(), assertion.value
                )
            )
        elif isinstance(assertion.value, float):
            self._nodes.append(
                self._create_float_delta_assert(assertion.source, assertion.value)
            )
        else:
            self._nodes.append(
                self._create_constant_assert(
                    assertion.source, ast.Eq(), assertion.value
                )
            )

    def visit_none_assertion(self, assertion: na.NoneAssertion) -> None:
        """
        Creates an assertion of form "assert var0 is None" or "assert var0 is not None".

        Args:
            assertion: the assertion that is visited.
        """
        self._nodes.append(
            self._create_constant_assert(
                assertion.source, ast.Is() if assertion.value else ast.IsNot(), None
            )
        )

    def visit_complex_assertion(self, assertion) -> None:
        """
        Creates an assertion of form "assert var0 == obj" or
        "assert var0 == collection".

        Args:
            assertion: the assertion that is visited.
        """
        if tu.is_primitive_type(type(assertion.value)):
            self.visit_primitive_assertion(assertion)
        elif tu.is_none_type(type(assertion.value)):
            self.visit_none_assertion(assertion)
        elif tu.is_enum(type(assertion.value)):
            self._create_enum_assertion(assertion.source, assertion.value)
        else:
            self._create_comparison_object(assertion.value)
            self._create_object_assertion(assertion.source)

    def visit_field_assertion(self, assertion) -> None:
        """
        Creates an assertion of form "assert var0.field == value".

        Args:
            assertion: the assertion that is visited.
        """
        if tu.is_primitive_type(type(assertion.value)):
            self._create_field_assertion_primitive(
                assertion.source,
                assertion.value,
                assertion.field,
                assertion.module,
                assertion.owners,
            )
        elif tu.is_none_type(type(assertion.value)):
            self._create_field_none_assertion(
                assertion.source,
                assertion.value,
                assertion.field,
                assertion.module,
                assertion.owners,
            )
        elif tu.is_enum(type(assertion.value)):
            self._create_field_enum_assertion(
                assertion.source,
                assertion.value,
                assertion.field,
                assertion.module,
                assertion.owners,
            )
        else:
            if tu.is_collection_type(type(assertion.value)):
                self._create_collection(assertion.value)
            else:
                self._create_comparison_object(assertion.value)
            self._create_field_assertion(
                assertion.source,
                assertion.field,
                assertion.module,
                assertion.owners,
            )

    def _create_constant_assert(
        self, var: vr.VariableReference, operator: ast.cmpop, value: Any
    ) -> ast.Assert:
        left = au.create_var_name(self._variable_names, var, load=True)
        comp = au.create_ast_constant(value)
        return au.create_ast_assert(au.create_ast_compare(left, operator, comp))

    def _create_float_delta_assert(
        self, var: vr.VariableReference, value: Any
    ) -> ast.Assert:
        left = au.create_var_name(self._variable_names, var, load=True)
        comp = self._construct_float_comparator(au.create_ast_constant(value))
        return au.create_ast_assert(au.create_ast_compare(left, ast.Eq(), comp))

    def _create_comparison_object(self, value) -> None:
        if tu.is_collection_type(type(value)):
            self._create_collection(value)
        elif isinstance(value, array.ArrayType):
            self._create_comparison_array(value)
        else:
            self._create_object(value)

            for field, field_val in vars(value).items():
                self._create_init_field(field, field_val)

    def _create_assertion(self, left, operator, comp):
        test = au.create_ast_compare(left, operator, comp)
        self._nodes.append(au.create_ast_assert(test))

    def _create_object_assertion(self, var: vr.VariableReference) -> None:
        left = au.create_var_name(self._variable_names, var, load=True)
        comp = au.create_ast_name(self._get_current_comparison_object())
        self._create_assertion(left, ast.Eq(), comp)
        self._pop_current_comparison_object()

    # pylint: disable=too-many-arguments
    def _create_field_assertion_primitive(
        self,
        var: vr.VariableReference,
        value: Any,
        field: str,
        module: str,
        owners: List[str],
    ) -> None:
        left = self._construct_field_attribute(var, field, module, owners)
        comp = au.create_ast_constant(value)
        if isinstance(value, float):
            comp_float = self._construct_float_comparator(comp)
            self._create_assertion(left, ast.Eq(), comp_float)
        elif isinstance(value, bool):
            self._create_assertion(left, ast.Is(), comp)
        else:
            self._create_assertion(left, ast.Eq(), comp)

    def _construct_float_comparator(self, comp):
        self._common_modules.add("pytest")
        float_precision = config.configuration.test_case_output.float_precision
        func = au.create_ast_attribute("approx", au.create_ast_name("pytest"))
        keywords = [
            au.create_ast_keyword("abs", au.create_ast_constant(float_precision)),
            au.create_ast_keyword("rel", au.create_ast_constant(float_precision)),
        ]
        comp_float = au.create_ast_call(func, [comp], keywords)
        return comp_float

    def _create_enum_assertion(self, var: vr.VariableReference, value: Any) -> None:
        enum_attr = self._construct_enum_attr(value)
        comp = au.create_ast_attribute(value.name, enum_attr)
        left = au.create_var_name(self._variable_names, var, load=True)
        self._nodes.append(
            au.create_ast_assert(au.create_ast_compare(left, ast.Eq(), comp))
        )

    def _create_field_enum_assertion(
        self,
        var: vr.VariableReference,
        value: Any,
        field: str,
        module: str,
        owners: List[str],
    ) -> None:
        left = self._construct_field_attribute(var, field, module, owners)
        comp = au.create_ast_attribute(value.name, self._construct_enum_attr(value))
        self._create_assertion(left, ast.Eq(), comp)

    def _create_field_none_assertion(
        self,
        var: vr.VariableReference,
        value: Any,
        field: str,
        module: str,
        owners: List[str],
    ):
        left = self._construct_field_attribute(var, field, module, owners)
        comp = au.create_ast_constant(value)
        self._create_assertion(left, ast.Is() if value is None else ast.IsNot(), comp)

    def _create_field_assertion(
        self, var: vr.VariableReference, field: str, module: str, owners: List[str]
    ) -> None:
        left = self._construct_field_attribute(var, field, module, owners)
        comp = au.create_ast_name(self._get_current_comparison_object())
        self._create_assertion(left, ast.Eq(), comp)
        self._pop_current_comparison_object()

    def _construct_field_attribute(
        self,
        var: Optional[vr.VariableReference],
        field: str,
        module: Optional[str],
        owners: List[str],
    ) -> ast.Attribute:
        if var is not None and module is None:
            # Attribute
            attr = au.create_var_name(self._variable_names, var, load=True)
        else:
            # Class variable or global field
            attr = au.create_ast_name(self._get_module(module))
        for owner in owners:
            attr = cast(Name, au.create_ast_attribute(owner, attr))
        return au.create_ast_attribute(field, attr)

    def _create_object(self, value) -> None:
        obj_id = self._get_comparison_object()

        # Create dummy inline class
        target = au.create_ast_name(obj_id, True)
        args = [
            au.create_ast_constant(""),
            au.create_ast_tuple([au.create_ast_name("object")]),
            au.create_ast_dict([], []),
        ]
        func_name = au.create_ast_name("type")
        call = au.create_ast_call(au.create_ast_call(func_name, args, []), [], [])
        self._nodes.append(au.create_ast_assign(target, call))

        # Assign right class type
        obj_name = au.create_ast_name(obj_id)
        attr = au.create_ast_attribute("__class__", obj_name, True)
        module_name = value.__class__.__module__
        module = au.create_ast_name(self._get_module(module_name))
        cls = au.create_ast_attribute(value.__class__.__name__, module)
        self._nodes.append(au.create_ast_assign(attr, cls))

    def _create_init_field(self, field, value) -> None:
        if tu.is_collection_type(type(value)):
            self._create_collection(value)
            val = au.create_ast_name(self._get_current_comparison_object())
            self._pop_current_comparison_object()
        elif tu.is_none_type(type(value)):
            val = cast(Name, au.create_ast_constant(None))
        elif tu.is_enum(type(value)):
            attr = au.create_ast_attribute(value.name, self._construct_enum_attr(value))
            val = cast(Name, attr)
        elif not tu.is_primitive_type(type(value)):
            self._create_comparison_object(value)
            val = au.create_ast_name(self._get_current_comparison_object())
            self._pop_current_comparison_object()
        else:
            val = cast(Name, au.create_ast_constant(value))
        obj = au.create_ast_name(self._get_current_comparison_object())
        attr = au.create_ast_attribute(field, obj, True)
        self._nodes.append(au.create_ast_assign(attr, val))

    def _construct_enum_attr(self, value) -> ast.Attribute:
        module = self._get_module(value.__class__.__module__)
        enum_name = value.__class__.__name__
        return au.create_ast_attribute(enum_name, au.create_ast_name(module))

    def _create_collection(self, value) -> None:
        obj_id = self._get_comparison_object()
        if tu.is_list(value):
            self._create_list(value, obj_id)
        elif tu.is_set(value):
            self._create_set(value, obj_id)
        elif tu.is_dict(value):
            self._create_dict(value, obj_id)
        elif tu.is_tuple(value):
            self._create_tuple(value, obj_id)

    def _create_comparison_array(self, value) -> None:
        obj_id = self._get_comparison_object()
        self._create_collection(value.tolist())
        target = au.create_ast_name(obj_id, True)
        self._common_modules.add("array")
        func_attr_name = au.create_ast_name("array")
        func_attr = au.create_ast_attribute("array", func_attr_name)
        constant = au.create_ast_constant(value.typecode)
        arr = au.create_ast_name(self._get_current_comparison_object())
        self._pop_current_comparison_object()
        call = au.create_ast_call(func_attr, [constant, arr], [])
        self._nodes.append(au.create_ast_assign(target, call))

    def _create_list(self, value: List[Any], obj_id: str) -> None:
        elts = self._construct_collection_elts(value)
        target = au.create_ast_name(obj_id)
        assg_val = au.create_ast_list(elts)
        self._nodes.append(au.create_ast_assign(target, assg_val))

    def _create_dict(self, value: Dict[Any, Any], obj_id: str) -> None:
        keys = self._construct_collection_elts(value.keys())
        values = self._construct_collection_elts(value.values())
        target = au.create_ast_name(obj_id)
        assg_val = au.create_ast_dict(keys, values)
        self._nodes.append(au.create_ast_assign(target, assg_val))

    def _create_set(self, value: Set[Any], obj_id: str) -> None:
        elts = self._construct_collection_elts(value)
        target = au.create_ast_name(obj_id)
        assg_val = au.create_ast_set(elts)
        self._nodes.append(au.create_ast_assign(target, assg_val))

    def _create_tuple(self, value: Tuple[Any], obj_id) -> None:
        elts = self._construct_collection_elts(value)
        target = au.create_ast_name(obj_id)
        assg_val = au.create_ast_tuple(elts)
        self._nodes.append(au.create_ast_assign(target, assg_val))

    def _construct_collection_elts(
        self, value: Any
    ) -> List[Union[Constant, Name, Attribute]]:
        elts: List[Union[Constant, Name, Attribute]] = []
        for item in value:
            if tu.is_primitive_type(type(item)) or tu.is_none_type(type(item)):
                elts.append(au.create_ast_constant(item))
            elif tu.is_enum(type(item)):
                attr = au.create_ast_attribute(
                    item.name, self._construct_enum_attr(item)
                )
                elts.append(attr)
            else:
                if tu.is_collection_type(type(item)):
                    self._create_collection(item)
                else:
                    self._create_comparison_object(item)
                elts.append(au.create_ast_name(self._get_current_comparison_object()))
                self._pop_current_comparison_object()
        return elts

    def _pop_current_comparison_object(self) -> None:
        self._obj_stack.pop()

    def _get_current_comparison_object(self) -> str:
        return self._obj_stack[-1]

    def _get_comparison_object(self) -> str:
        obj_id = self._get_comparison_object_name()
        AssertionToAstVisitor._obj_index += 1
        self._obj_stack.append(obj_id)
        return obj_id

    def _get_comparison_object_name(self) -> str:
        return self.COMPARISON_OBJECT_IDENTIFIER + str(AssertionToAstVisitor._obj_index)

    def _get_module(self, module_name: Optional[str]) -> str:
        return self._module_aliases.get_name(module_name)
