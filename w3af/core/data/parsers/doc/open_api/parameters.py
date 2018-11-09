# -*- coding: UTF-8 -*-
"""
requests.py

Copyright 2017 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import copy
import random
import datetime
import yaml

from bravado_core.operation import Operation

from w3af.core.data.fuzzer.form_filler import (smart_fill,
                                               smart_fill_file)


class OpenAPIParamResolutionException(Exception):
    pass


class ParameterValueParsingError(Exception):

    def __init__(self, message):
        super(ParameterValueParsingError, self).__init__(message)


class ParameterValues(object):

    def __init__(self):
        self._values = {}

    def is_empty(self):
        """
        :return: True if no parameter values were set, false otherwise.
        """
        return not self._values

    def get(self, path, name):
        """
        Gets values for a parameter of API endpoint.
        :param path: A path to the API endpoint.
        :param name: A name of the parameter.
        :return: A list of values assigned to the parameter.
        """
        values = self._values.get(self.key(path, name), None)
        if not values:
            return []
        return list(values)

    def set(self, path, name, values):
        """
        Sets values for a parameter of API endpoint.
        :param path: A path to the API endpoint.
        :param name: A name of the parameter.
        :param values: A list of values to be assigned to the parameter.
        """
        if not isinstance(values, list):
            raise ValueError('values is not a list')
        self._values[self.key(path, name)] = list(values)

    @staticmethod
    def key(path, name):
        return '%s|%s' % (path, name)

    def load_from_file(self, filename):
        """
        Loads parameter values from a YAML file.
        :param filename: A path to the file to be loaded.
        """
        with open(filename, 'r') as content:
            return self.load_from_string(content)

    def load_from_string(self, string):
        """
        Loads parameter values from YAML.
        :param string: Definition of parameter values in YAML.
        """
        try:
            content = yaml.load(string)
        except yaml.YAMLError, e:
            raise ParameterValueParsingError(e)

        if not isinstance(content, list):
            raise ParameterValueParsingError('root is not a list')

        for item in content:
            if 'path' not in item:
                raise ParameterValueParsingError('item does not have path')
            if 'parameters' not in item:
                raise ParameterValueParsingError('item does not have parameters')
            for parameter in item['parameters']:
                if 'name' not in parameter:
                    raise ParameterValueParsingError('parameter does not have name')
                if 'values' not in parameter:
                    raise ParameterValueParsingError('parameter does not have values')
                if not isinstance(parameter['values'], list):
                    raise ParameterValueParsingError('values is not a list')
                self.set(item['path'], parameter['name'], parameter['values'])


class ParameterHandler(object):

    DEFAULT_VALUES_BY_TYPE = {'int64': 42,
                              'int32': 42,
                              'integer': 42,
                              'float': 4.2,
                              'double': 4.2,
                              'date': datetime.date(2017, 06, 30),
                              'date-time': datetime.datetime(2017, 06, 30, 23, 59, 45),
                              'boolean': True}

    def __init__(self, spec, operation):
        """
        :param spec: The parsed specification. We need this to get the param value
                     in special cases where it's type is defined to a Model.
        :param operation: The REST API operation, eg. addStoreItem
        """
        self.spec = spec
        self.operation = operation

    def set_operation_params(self, optional=False, custom_parameter_values=ParameterValues()):
        """
        This is the main entry point. We return a set with the required and
        optional parameters for the provided operation / specification.

        The method tries to fill out the parameters with the best values.
        But a caller can provide context-specific values. In this case,
        the method prefers them while filling out the parameters of the operation.
        If the caller provided multiple values for parameters,
        then the method tries to enumerate all possible combinations of parameters.

        For example, let's consider an operation which takes two required parameters,
        and a caller provided the following values for each of them:
          * param_one -> foo, bar
          * param_two -> 1984, 42
        Then, the method generates 4 new operations with the following values:
          * param_one = foo, param_two = 1984
          * param_one = foo, param_two = 42
          * param_one = bar, param_two = 1984
          * param_one = bar, param_two = 42

        :param optional: Should we set the values for the optional parameters?
        :param custom_parameter_values: Sets context-specific values for parameters
                                        used by the API endpoints.
        """
        self._fix_common_spec_issues()

        # Make a copy of the operation
        operation = self._copy(self.operation)

        for parameter_name, parameter in operation.params.iteritems():
            # We make sure that all parameters have a fill attribute
            parameter.fill = None

            if self._should_skip_setting_param_value(parameter, optional):
                continue

            self._try_to_set_param_value(parameter)

        # Return a list with single operation
        # if no context-specific values were provided.
        operations = [operation]
        if custom_parameter_values.is_empty():
            return operations

        # If the caller provided some context-specific values,
        # then prefer them, and enumerate all combinations.
        for parameter_name, parameter in operation.params.iteritems():

            if self._should_skip_setting_param_value(parameter, optional):
                continue

            values = custom_parameter_values.get(operation.path_name, parameter.name)
            if values:
                operations = self._set_custom_parameter_values(operations, parameter_name, values)

        return operations

    @staticmethod
    def _set_custom_parameter_values(operations, parameter_name, values):
        """
        For all operations in the list, assigns the values to the specified parameter.
        In case of multiple values, the method creates copies of operations for each value.
        :param operations: The list of operations.
        :param parameter_name: A name of the parameter.
        :param values: The list of values.
        :return: A new list of operations with assigned values.
        """
        if not values:
            return operations

        # If we have a single value, then just update all operations with it.
        if len(values) == 1:
            for operation in operations:
                operation.params[parameter_name].fill = values[0]
            return operations

        # If we have multiple values, then make a copy of each operation for each value
        extended_operations = []
        for operation in operations:
            for value in values:
                clone = ParameterHandler._copy(operation)
                clone.params[parameter_name].fill = value
                extended_operations.append(clone)

        return extended_operations

    @staticmethod
    def _copy(operation):
        """
        Creates a copy of the operation.

        Note: deepcopy() may cause problems with debugging
        when it's used with complex objects which contain loops.
        Basically, pdb can start skipping breakpoints after a call to deepcopy().
        Most probably, no complex objects will be used for 'fill' field,
        so no problem with debugging should occur. If such a problem occurs,
        we need to avoid using deepcopy() here.

        :param operation: The operation to be copied.
        :return: A clone of the operation.
        """
        clone = Operation.from_spec(operation.swagger_spec,
                                    operation.path_name,
                                    operation.http_method,
                                    operation.op_spec)

        for parameter_name, parameter in operation.params.iteritems():
            if hasattr(parameter, 'fill'):
                clone.params[parameter_name].fill = copy.deepcopy(parameter.fill)

        return clone

    def operation_has_optional_params(self):
        """
        :return: True if the operation has optional parameters
        """
        for parameter_name, parameter in self.operation.params.iteritems():
            if not parameter.required:
                return True

        return False

    def _fix_common_spec_issues(self):
        """
        Sometimes the openapi specification is manually written, modified by
        a developer, or automatically generated by a tool that will generate
        invalid OpenAPI documents.

        This method will try to fix some of the issues we've see in real life.

        :return: None, just modifies the self.operation and self.spec
        """
        self._fix_string_format()
        self._fix_string_with_invalid_format()
        self._fix_bad_default_for_number_type()

    def _fix_string_format(self):
        """
        Bravado core doesn't support using "string" as the format:

            {
                "default": "name,user_id",
                "type": "string",
                "name": "sort",
                "in": "query",
                "format": "string",         <--------- THIS
                "required": false,
                "description": "Sort order"
            }

        So this method will iterate through all the parameters in this
        operation and remove that.

        :return: None
        """
        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_type = parameter.param_spec.get('type', None)

            if param_format == 'string' and param_type == 'string':
                del parameter.param_spec['format']

    def _fix_string_with_invalid_format(self):
        """
        The specification [0] only allows some values for format when string
        type is used. Developers sometimes make this mistake:

            {
                "default": "",
                "type": "string",           <--------- THIS
                "name": "fields[Users]",
                "in": "query",
                "format": "int64",          <--------- THIS
                "required": false,
                "description": "Fields to be selected (csv)"
            }

        So this method will iterate through all the parameters in this
        operation and remove the invalid format.

        [0] https://swagger.io/specification/#dataTypes

        :return: None
        """
        invalid_formats = ['int32', 'int64', 'float', 'double', '']

        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_type = parameter.param_spec.get('type', None)

            if param_format in invalid_formats and param_type == 'string':
                del parameter.param_spec['format']

    def _fix_bad_default_for_number_type(self):
        """
        Sometimes developers set the default value to something that is not
        valid for the type / format they specify.

            {
                "default": "",              <--------- THIS
                "type": "string",
                "name": "fields[Users]",
                "in": "query",
                "format": "int64",          <--------- THIS
                "required": false,
                "description": "Fields to be selected (csv)"
            }

        >>> long('')
        ValueError: invalid literal for long() with base 10: ''

        Just set a default value of zero if an empty string is specified.

        :return: None
        """
        fix_formats = ['double', 'float', 'int32', 'int64']

        for parameter_name, parameter in self.operation.params.iteritems():

            param_format = parameter.param_spec.get('format', None)
            param_default = parameter.param_spec.get('default', None)

            if param_format not in fix_formats:
                continue

            if not isinstance(param_default, basestring):
                continue

            if param_default.isdigit():
                continue

            parameter.param_spec['default'] = 0

    def _try_to_set_param_value(self, parameter):
        """
        If the parameter has a default value, then we use that. If there is
        no value, we try to fill it with something that makes sense based on
        the parameter type and name.

        The value is set to the parameter.fill attribute

        :param parameter: The parameter for which we need to set a value
        :return: True if we were able to set the parameter value
        """
        #
        #   Easiest case, the parameter already has a default value
        #
        if parameter.default is not None:
            parameter.fill = parameter.default
            return True

        param_spec = parameter.param_spec

        value = self._get_param_value(param_spec)
        if value is not None:
            parameter.fill = value
            return True

        return False

    def _get_param_value(self, param_spec):
        """
        Receives a parameter specification and returns a valid value

        :param param_spec: The parameter specification
        :return: A valid value, string, int, dict, etc.
        """
        if 'schema' in param_spec:
            param_spec = param_spec['schema']

        value = self._get_param_value_for_primitive(param_spec)
        if value is not None:
            return value

        value = self._get_param_value_for_model(param_spec)
        if value is not None:
            return value

        # A default
        return 42

    def _get_param_value_for_type_and_spec(self, parameter_type, parameter_spec):
        """
        :param parameter_type: The type of parameter (string, int32, array, etc.)
        :param parameter_spec: The parameter spec
        :return: The parameter value
        """
        parameter_name = parameter_spec.get('name', None)

        # This handles the case where the value is an enum and can only be selected
        # from a predefined option list
        if 'enum' in parameter_spec:
            if parameter_spec['enum']:
                return parameter_spec['enum'][0]

        if parameter_type in ('integer', 'float', 'double', 'int32', 'int64'):
            _max = None
            _min = None

            if 'maximum' in parameter_spec:
                _max = parameter_spec['maximum']

            if 'minimum' in parameter_spec:
                _min = parameter_spec['minimum']

            # Only do something if max or min are set
            if _max is not None or _min is not None:
                _max = _max if _max is not None else 56
                _min = _min if _min is not None else 0

                # We always want to generate the same number for the same range
                r = random.Random()
                r.seed(1)

                return r.randint(_min, _max)

        default_value = self.DEFAULT_VALUES_BY_TYPE.get(parameter_type, None)
        if default_value is not None:
            return default_value

        if parameter_type == 'string':
            parameter_name = 'unknown' if parameter_name is None else parameter_name
            return smart_fill(parameter_name)

        if parameter_type == 'file':
            parameter_name = 'unknown' if parameter_name is None else parameter_name
            return smart_fill_file(parameter_name, 'cat.png')

    @staticmethod
    def _get_parameter_type(param_spec):
        """
        The parameter has a strong type:

            https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md

        Fetch it and return.
        """
        try:
            parameter_type = param_spec['format']
        except KeyError:
            try:
                parameter_type = param_spec['type']
            except KeyError:
                # This is not a primitive type, most likely a model
                return None

        return parameter_type

    def _get_param_value_for_primitive(self, param_spec):
        """
        Handle the cases where the parameter is a primitive: int, string, float, etc.

        :param param_spec: The parameter spec which (might or might not) be of a primitive type
        :return: The parameter we just modified
        """
        parameter_type = self._get_parameter_type(param_spec)
        if parameter_type is None:
            return None

        value = self._get_param_value_for_type_and_spec(parameter_type,
                                                        param_spec)
        if value is not None:
            return value

        #
        # Arrays are difficult to handle since they can contain complex data
        #
        value = self._get_param_value_for_array(param_spec)

        if value is not None:
            return value

        # We should never reach here! The parameter.fill value was never
        # modified!
        return None

    def _get_param_value_for_array(self, param_spec):
        """
        :param param_spec: The parameter spec
        :return: A python list (json array) containing values
        """
        if param_spec.get('type', None) != 'array':
            return None

        if param_spec.get('items', None) is None:
            # Potentially invalid array specification, we just return
            # an empty array
            return []

        # Do we have a default value which can be used?
        if 'default' in param_spec['items']:
            return [param_spec['items']['default']]

        #
        # The array definition is a little bit more complex than just
        # returning [some-primitive-type]. For example it might
        # look like this:
        #
        #     u'photoUrls': {u'items': {u'type': u'string'},
        #                    u'type': u'array',
        #
        # Where this is completely valid: ['http://abc/']
        #
        # Or like this:
        #
        #     u'ids': {u'items': {u'type': u'int32'},
        #              u'type': u'array',
        #
        # Where we need to fill with integers: [1, 3, 4]
        #
        # Or even worse... there is a model in the array:
        #
        #     u'tags': {u'items': {u'$ref': u'#/definitions/Tag',
        #                           'x-scope': [u'http://moth/swagger.json',
        #                                      u'http://moth/swagger.json#/definitions/Pet']},
        #               u'type': u'array',
        #
        # And we need to fill the array with one or more tags
        #
        item_param_spec = param_spec['items']

        value = self._get_param_value(item_param_spec)
        if value is not None:
            return [value]

        return []

    def _get_param_value_for_model(self, param_spec):
        """
        Each model attribute can be of a primitive type or another model.

        We need to dereference the model until we have primitives for each field
        (as seen in http://bigstickcarpet.com/swagger-parser/www/index.html#)
        and then fill the value for each primitive.

        :param param_spec: The parameter specification instance
        :return: The parameter with a modified default attribute
        """
        parameter_definition = self._get_object_definition(param_spec)
        created_object = self._create_object(parameter_definition)

        if created_object is not None:
            return created_object

        raise NotImplementedError

    def _get_object_definition(self, param_spec):
        """
        This method calls `_get_object_definition_impl` until the object
        definition is completely dereferenced.

        In the most common cases we call `_get_object_definition_impl` only
        a couple of times to resolve things like `$ref` and `allOf`.

        :param param_spec: The parameter specification instance
        :return: The object definition which needs to be created
        """
        return self._get_object_definition_impl(param_spec)

    def _merge_all_parts(self, all_parts):
        """
        https://swagger.io/docs/specification/data-models/oneof-anyof-allof-not/

        When we receive an allOf we just merge all the properties from the
        different schemas / definitions / references / models into one big
        dict and return it.

        The output of this method looks like:

                {u'title': u'Pet',
                 u'x-model': u'Pet',
                 u'type': u'object',
                 u'properties': {u'age': {u'type': u'integer', u'format': u'int32'}},
                 u'required': [u'name']}

        :param all_parts: A list containing the `allOf`
        :return: The definition as shown above
        """
        merged = {'required': [],
                  'properties': {},
                  'type': 'object'}

        for part in all_parts:
            object_definition = self._get_object_definition_impl(part)

            if 'required' in object_definition:
                for required in object_definition['required']:
                    merged['required'].append(required)

            if 'properties' in object_definition:
                for property_name, property_def in object_definition['properties'].iteritems():
                    merged['properties'][property_name] = property_def

        return merged

    def _get_object_definition_impl(self, param_spec):
        """
        :param param_spec: The parameter specification instance
        :return: The object definition which needs to be created
        """
        if '$ref' in param_spec:
            ref = {'$ref': param_spec['$ref']}
            param_spec = self.spec.deref(ref)

        if 'allOf' in param_spec:
            all_parts = param_spec['allOf']
            param_spec = self._merge_all_parts(all_parts)

        if 'schema' in param_spec:
            if '$ref' in param_spec['schema']:
                ref = {'$ref': param_spec['schema']['$ref']}
                param_spec = self.spec.deref(ref)
            else:
                # The definition is not a reference, the param_spec['schema'] looks like:
                #
                # {u'title': u'Pet',
                #  u'x-model': u'Pet',
                #  u'type': u'object',
                #  u'properties': {u'age': {u'type': u'integer', u'format': u'int32'}},
                #  u'required': [u'name']}
                param_spec = param_spec['schema']

        if 'type' in param_spec:
            if param_spec['type'] == 'object':
                # In this case the param_spec holds these values:
                #
                # {u'x-model': u'Pet Owner',
                #  u'name': u'owner',
                #  u'title': u'Pet Owner',
                #  u'required': [u'name'],
                #  u'type': u'object',
                #  u'properties': '...'}
                pass

        return param_spec

    def _create_object(self, param_spec):
        """
        Takes the output of a swagger_spec.deref() cal and creates an object.

        The output of swagger_spec.deref looks like:

        {u'required': [u'name'],
         u'type': u'object',
         u'properties': {u'tag': {u'type': u'string'},
                         u'name': {u'type': u'string'}},
         u'x-model': u'http:....www.w3af.com..swagger.json|..definitions..Pet'}

        :return: A dict containing all the fields specified in properties.
        """
        if param_spec.get('type', None) != 'object':
            return {}

        created_object = {}

        for property_name, property_data in param_spec['properties'].iteritems():

            # This helps us choose a better value for filling the parameter
            if 'name' not in property_data:
                property_data['name'] = property_name

            value = self._get_param_value(property_data)
            created_object[property_name] = value

        return created_object

    @staticmethod
    def _should_skip_setting_param_value(parameter, optional):
        """
        Checks if we should set a value to a parameter.

        :param parameter: The parameter which we need to check.
        :param optional: Should we set the values for the optional parameters?
        :return: True if we should set a value for the parameter, False otherwise.
        """
        if ParameterHandler._is_header_with_default(parameter):
            return False

        if not parameter.required and not optional:
            return True

        return False

    @staticmethod
    def _is_header_with_default(parameter):
        """
        Checks if the parameter is a header and it's spec has a default value.

        :param parameter: The parameter which we need to check.
        :return: True if the parameter is a header with defined default value, False otherwise.
        """
        return ParameterHandler._is_header(parameter) and ParameterHandler._parameter_has_default(parameter)

    @staticmethod
    def _is_header(parameter):
        """
        Checks if the parameter is a header.

        :param parameter: The parameter which we need to check.
        :return: True if the parameter is a header, False otherwise.
        """
        return parameter.param_spec.get('in', None) == 'header'

    @staticmethod
    def _parameter_has_default(parameter):
        """
        Checks if the parameter has a default value.

        :param parameter: The parameter which we need to check.
        :return: True if the parameter has a default value, False otherwise.
        """
        if ParameterHandler._spec_has_default(parameter.param_spec):
            return True

        schema = parameter.param_spec.get('schema', None)
        if schema is not None:
            return ParameterHandler._spec_has_default(schema)

        return False

    @staticmethod
    def _spec_has_default(spec):
        """
        Checks if the spec defines a default value in 'default' or 'enum' attributes.
        :param spec: The spec which we need to check.
        :return: True is the spec defines a default value, False otherwise.
        """
        default = spec.get('default', None)
        if default is not None:
            return True

        enum = spec.get('enum', None)
        if enum is not None:
            return len(enum) > 0

        return False
