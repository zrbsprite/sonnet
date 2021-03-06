# Copyright 2017 The Sonnet Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or  implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

"""Tests for sonnet.python.modules.base."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from functools import partial
import pickle
import numpy as np
import six
from sonnet.python.modules import base
import tensorflow as tf

logging = tf.logging


class ModuleWithClassKeys(base.AbstractModule):
  """Dummy module that defines some keys as class attributes."""
  POSSIBLE_INITIALIZER_KEYS = {"foo", "bar"}


class ModuleWithNoInitializerKeys(base.AbstractModule):
  """Dummy module without any intiailizer keys."""
  pass


class ModuleWithCustomInitializerKeys(base.AbstractModule):
  """Dummy module that overrides get_possible_initializer_keys."""

  @classmethod
  def get_possible_initializer_keys(cls, custom_key):
    return {"foo"} if custom_key else {"bar"}


class IdentityModule(base.AbstractModule):
  """Sonnet module that builds a single `tf.identity` op."""

  def _build(self, inputs):
    return tf.identity(inputs)


class NoInitIdentityModule(base.AbstractModule):
  """Sonnet module that inherits `base.AbstractModule.__init__`."""

  def _build(self, inputs):
    return tf.identity(inputs)


class NoSuperInitIdentityModule(base.AbstractModule):
  """Sonnet module that doesn't call `base.AbstractModule.__init__`."""

  def __init__(self):
    pass  # Don't call superclass initializer.

  def _build(self, inputs):
    return tf.identity(inputs)


class InternalFunctionTest(tf.test.TestCase):

  def testToSnakeCase(self):
    test_cases = [
        ("UpperCamelCase", "upper_camel_case"),
        ("lowerCamelCase", "lower_camel_case"),
        ("endsWithXYZ", "ends_with_xyz"),
        ("already_snake_case", "already_snake_case"),
        ("__private__", "private"),
        ("LSTMModule", "lstm_module"),
        ("version123p56vfxObject", "version_123p56vfx_object"),
        ("version123P56VFXObject", "version_123p56vfx_object"),
        ("versionVFX123P56Object", "version_vfx123p56_object"),
        ("versionVfx123P56Object", "version_vfx_123p56_object"),
        ("lstm1", "lstm_1"),
        ("LSTM1", "lstm1"),
    ]
    for camel_case, snake_case in test_cases:
      actual = base._to_snake_case(camel_case)
      self.assertEqual(actual, snake_case, "_to_snake_case(%s) -> %s != %s" %
                       (camel_case, actual, snake_case))


class AbstractModuleTest(tf.test.TestCase):

  def testInitializerKeys(self):
    keys = ModuleWithClassKeys.get_possible_initializer_keys()
    self.assertEqual(keys, {"foo", "bar"})
    keys = ModuleWithNoInitializerKeys.get_possible_initializer_keys()
    self.assertEqual(keys, set())
    msg = ("missing 1 required positional argument" if six.PY3
           else "takes exactly 2 arguments")
    self.assertRaisesRegexp(
        TypeError, msg,
        ModuleWithCustomInitializerKeys.get_possible_initializer_keys)
    keys = ModuleWithCustomInitializerKeys.get_possible_initializer_keys(True)
    self.assertEqual(keys, {"foo"})
    keys = ModuleWithCustomInitializerKeys.get_possible_initializer_keys(False)
    self.assertEqual(keys, {"bar"})

  def testMultipleGraphs(self):
    id_mod = IdentityModule(name="identity")
    # gpylint incorrectly thinks IdentityModule is not callable, so disable.
    # pylint: disable=not-callable
    with tf.Graph().as_default() as graph:
      id_mod(tf.placeholder(dtype=tf.float32, shape=[42]))
      self.assertEqual(id_mod._graph, graph)

    with tf.Graph().as_default():
      with self.assertRaisesRegexp(base.DifferentGraphError,
                                   "Cannot connect module"):
        id_mod(tf.placeholder(dtype=tf.float32, shape=[42]))
    # pylint: enable=not-callable

  def testNameScopeRecording(self):
    id_mod = IdentityModule(name="foo")

    # Connect inside different name scope contexts, check that each is recorded.
    # pylint: disable=not-callable
    id_mod(tf.placeholder(dtype=tf.float32, shape=[22]))
    self.assertIn(id_mod.name_scopes, (("foo",), ("foo_1",)))
    with tf.name_scope("blah"):
      id_mod(tf.placeholder(dtype=tf.float32, shape=[23]))
    self.assertIn(id_mod.name_scopes,
                  (("foo", "blah/foo"), ("foo_1", "blah/foo")))
    with tf.name_scope("baz"):
      id_mod(tf.placeholder(dtype=tf.float32, shape=[24]))
    # pylint: enable=not-callable
    self.assertIn(id_mod.name_scopes,
                  (("foo", "blah/foo", "baz/foo"),
                   ("foo_1", "blah/foo", "baz/foo")))

  def testSubgraphsRecording(self):
    id_mod = IdentityModule(name="foo")

    with self.assertRaisesRegexp(base.NotConnectedError,
                                 "not instantiated yet"):
      id_mod.last_connected_subgraph()

    # pylint: disable=not-callable
    inputs = tf.placeholder(dtype=tf.float32, shape=[21])
    outputs = id_mod(inputs)
    with tf.name_scope("blah"):
      blah_inputs = tf.placeholder(dtype=tf.float32, shape=[22])
      blah_outputs = id_mod(blah_inputs)
    with tf.name_scope("baz"):
      baz_inputs = tf.placeholder(dtype=tf.float32, shape=[23])
      baz_outputs = id_mod(baz_inputs)
    # pylint: enable=not-callable
    subgraphs = id_mod.connected_subgraphs
    self.assertEqual(id_mod.last_connected_subgraph.name_scope, "baz/foo")
    self.assertIs(id_mod.last_connected_subgraph, subgraphs[2])
    self.assertIs(subgraphs[0].builder, id_mod)
    self.assertIn(subgraphs[0].name_scope, ("foo", "foo_1"))
    self.assertEqual(subgraphs[1].name_scope, "blah/foo")
    self.assertEqual(subgraphs[2].name_scope, "baz/foo")
    self.assertIs(subgraphs[0].inputs.args[0], inputs)
    self.assertIs(subgraphs[1].inputs.args[0], blah_inputs)
    self.assertIs(subgraphs[2].inputs.args[0], baz_inputs)
    self.assertIs(subgraphs[0].outputs, outputs)
    self.assertIs(subgraphs[1].outputs, blah_outputs)
    self.assertIs(subgraphs[2].outputs, baz_outputs)

  def testInitNoNamedArgs(self):
    """Tests if calling __init__ without named args raises a ValueError."""
    with self.assertRaises(ValueError):
      NoInitIdentityModule("foobar")

  def testInitInvalidTypeArgs(self):
    """Tests if calling __init__ without a string name raises a TypeError."""
    with self.assertRaises(TypeError):
      NoInitIdentityModule(name=123)

  def testInitNoArgs(self):
    """Tests if calling __init__ with no args uses correct defaults."""
    module = NoInitIdentityModule()
    self.assertEqual(module.module_name, "no_init_identity_module")

  def testInitNoSuper(self):
    """Tests if a __call__ with no __init__ raises an error."""
    module = NoSuperInitIdentityModule()
    with self.assertRaises(base.NotInitializedError):
      module(tf.constant([1]))  # pylint: disable=not-callable

  def testPicklingNotSupported(self):
    module = IdentityModule()
    with self.assertRaisesRegexp(base.NotSupportedError,
                                 "cannot be serialized"):
      # Writing the object to a string will fail.
      pickle.dumps(module)


def _make_model_with_params(inputs, output_size):
  weight_shape = [inputs.get_shape().as_list()[-1], output_size]
  weight = tf.get_variable("w", shape=weight_shape, dtype=inputs.dtype)
  return tf.matmul(inputs, weight)


class ModuleTest(tf.test.TestCase):

  def testFunctionType(self):
    with self.assertRaises(TypeError) as cm:
      base.Module(build="not_a_function")

    self.assertEqual(str(cm.exception), "Input 'build' must be callable.")

  def testSharing(self):
    batch_size = 3
    in_size = 4
    inputs1 = tf.placeholder(tf.float32, shape=[batch_size, in_size])
    inputs2 = tf.placeholder(tf.float32, shape=[batch_size, in_size])

    model = base.Module(build=partial(_make_model_with_params, output_size=10))
    outputs1 = model(inputs1)
    outputs2 = model(inputs2)
    input_data = np.random.rand(batch_size, in_size)

    with self.test_session() as sess:
      sess.run(tf.global_variables_initializer())
      outputs1, outputs2 = sess.run(
          [outputs1, outputs2],
          feed_dict={inputs1: input_data,
                     inputs2: input_data})
      self.assertAllClose(outputs1, outputs2)


if __name__ == "__main__":
  tf.test.main()
