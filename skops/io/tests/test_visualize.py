"""Tests for skops.io.visualize"""

from unittest.mock import Mock, patch

import pytest
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import (
    FunctionTransformer,
    MinMaxScaler,
    PolynomialFeatures,
    StandardScaler,
)

import skops.io as sio


class TestVisualizeTree:
    @pytest.fixture
    def simple(self):
        return MinMaxScaler(feature_range=(-555, 123))

    @pytest.fixture
    def pipeline(self):
        def unsafe_function(x):
            return x

        # fmt: off
        pipeline = Pipeline([
            ("features", FeatureUnion([
                ("scaler", StandardScaler()),
                ("scaled-poly", Pipeline([
                    ("polys", FeatureUnion([
                        ("poly1", PolynomialFeatures()),
                        ("poly2", PolynomialFeatures(degree=3, include_bias=False))
                    ])),
                    ("square-root", FunctionTransformer(unsafe_function)),
                    ("scale", MinMaxScaler()),
                ])),
            ])),
            ("clf", LogisticRegression(random_state=0, solver="liblinear")),
        ]).fit([[0, 1], [2, 3], [4, 5]], [0, 1, 2])
        # fmt: on
        return pipeline

    @pytest.mark.parametrize("show", ["all", "trusted", "untrusted"])
    def test_print_simple(self, simple, show, capsys):
        file = sio.dumps(simple)
        sio.visualize(file, show=show)

        # Output is the same for "all" and "trusted" because all nodes are
        # trusted. Colors are not recorded by capsys.
        expected = [
            "root: sklearn.preprocessing._data.MinMaxScaler",
            "└── attrs: builtins.dict",
            "    ├── feature_range: builtins.tuple",
            "    │   ├── content: json-type(-555)",
            "    │   └── content: json-type(123)",
            "    ├── copy: json-type(true)",
            "    ├── clip: json-type(false)",
            '    └── _sklearn_version: json-type("{}")'.format(sklearn.__version__),
        ]
        if show == "untrusted":
            # since no untrusted, only show root
            expected = expected[:1]

        stdout, _ = capsys.readouterr()
        assert stdout.strip() == "\n".join(expected)

    def test_print_pipeline(self, pipeline, capsys):
        file = sio.dumps(pipeline)
        sio.visualize(file)

        # no point in checking the whole output with > 120 lines
        expected_start = [
            "root: sklearn.pipeline.Pipeline",
            "└── attrs: builtins.dict",
            "    ├── steps: builtins.list",
            "    │   ├── content: builtins.tuple",
            '    │   │   ├── content: json-type("features")',
        ]
        expected_end = [
            "    ├── memory: json-type(null)",
            "    ├── verbose: json-type(false)",
            '    └── _sklearn_version: json-type("{}")'.format(sklearn.__version__),
        ]

        stdout, _ = capsys.readouterr()
        assert stdout.startswith("\n".join(expected_start))
        assert stdout.rstrip().endswith("\n".join(expected_end))

    def test_unsafe_nodes(self, pipeline):
        file = sio.dumps(pipeline)
        nodes = []

        def sink(nodes_iter, *args, **kwargs):
            nodes.extend(nodes_iter)

        sio.visualize(file, sink=sink)
        nodes_self_unsafe = [node for node in nodes if not node.is_self_safe]
        nodes_unsafe = [node for node in nodes if not node.is_safe]

        # there are currently 2 unsafe nodes, a numpy int and the custom
        # functions. The former might be considered safe in the future, in which
        # case this test needs to be changed.
        assert len(nodes_self_unsafe) == 2
        assert nodes_self_unsafe[0].val == "numpy.int64"
        assert nodes_self_unsafe[1].val == "test_visualize.unsafe_function"

        # it's not easy to test the number of indirectly unsafe nodes, because
        # it will depend on the nesting structure; we can only be sure that it's
        # more than 2, and one of them should be the FunctionTransformer
        assert len(nodes_unsafe) > 2
        assert any("FunctionTransformer" in node.val for node in nodes_unsafe)

    @pytest.mark.parametrize(
        "trusted", [True, ["numpy.int64", "test_visualize.unsafe_function"]]
    )
    def test_all_nodes_trusted(self, pipeline, trusted, capsys):
        # The pipeline contains untrusted type(s), but if we pass trusted=True,
        # it is not considered untrusted anymore
        # TODO: remove numpy.int64 from trusted once it's trusted by default
        file = sio.dumps(pipeline)
        sio.visualize(file, show="untrusted", trusted=trusted)
        expected = "root: sklearn.pipeline.Pipeline"
        stdout, _ = capsys.readouterr()
        assert stdout.strip() == expected

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"use_colors": False},
            {"tag_unsafe": "<careful>", "color_unsafe": "blue"},
        ],
    )
    def test_custom_print_config_passed_to_sink(self, simple, kwargs):
        # check that arguments are passed to sink
        def my_sink(nodes_iter, show, **sink_kwargs):
            for key, val in kwargs.items():
                assert sink_kwargs[key] == val

        file = sio.dumps(simple)
        sio.visualize(file, sink=my_sink, **kwargs)

    def test_custom_tags(self, simple, capsys):
        class UnsafeType:
            pass

        simple.copy = UnsafeType

        file = sio.dumps(simple)
        sio.visualize(file, tag_safe="NICE", tag_unsafe="OHNO")
        expected = [
            "root: sklearn.preprocessing._data.MinMaxScaler NICE",
            "└── attrs: builtins.dict NICE",
            "    ├── feature_range: builtins.tuple NICE",
            "    │   ├── content: json-type(-555) NICE",
            "    │   └── content: json-type(123) NICE",
            "    ├── copy: test_visualize.UnsafeType OHNO",
            "    ├── clip: json-type(false) NICE",
            '    └── _sklearn_version: json-type("{}") NICE'.format(
                sklearn.__version__
            ),
        ]

        stdout, _ = capsys.readouterr()
        assert stdout.strip() == "\n".join(expected)

    def test_custom_colors(self, simple):
        # test that custom colors are used in node representation, requires rich
        # to work
        pytest.importorskip("rich")

        class UnsafeType:
            pass

        simple.copy = UnsafeType
        file = sio.dumps(simple)

        # Colors are not recorded by capsys, so we cannot use it and must mock
        # printing
        mock_print = Mock()
        with patch("rich.print", mock_print):
            sio.visualize(
                file,
                color_safe="black",
                color_unsafe="cyan",
                color_child_unsafe="orange3",
            )

        mock_print.assert_called()

        calls = mock_print.call_args_list
        # The root node is indirectly unsafe through child
        assert (
            calls[0].args[0]
            == "root: [orange3]sklearn.preprocessing._data.MinMaxScaler"
        )
        # 'feature_range' is safe
        assert calls[6].args[0] == " feature_range: [black]builtins.tuple"
        # 'copy' is unsafe
        assert calls[15].args[0] == " copy: [cyan]test_visualize.UnsafeType [UNSAFE]"

    @pytest.mark.usefixtures("rich_not_installed")
    def test_no_colors_if_rich_not_installed(self, simple):
        # this test is similar to the previous one, except that we test that the
        # colors are *not* used if rich is not installed
        file = sio.dumps(simple)

        # don't use capsys, because it wouldn't capture the colors, thus need to
        # use mock
        mock_print = Mock()
        with patch("builtins.print", mock_print):
            sio.visualize(
                file,
                color_safe="black",
                color_unsafe="cyan",
                color_child_unsafe="orange3",
            )
        mock_print.assert_called()

        # check that none of the colors are being used
        colors = ("black", "cyan", "orange3")
        for call in mock_print.call_args_list:
            for color in colors:
                assert color not in call.args[0]

    def test_no_colors_if_use_colors_false(self, simple):
        # this test is similar to the previous one, except that we test that the
        # colors are *not* used, even if rich is installed, when passing
        # use_colors=False
        file = sio.dumps(simple)

        # don't use capsys, because it wouldn't capture the colors, thus need to
        # use mock
        mock_print = Mock()
        with patch("rich.print", mock_print):
            sio.visualize(
                file,
                color_safe="black",
                color_unsafe="cyan",
                color_child_unsafe="orange3",
                use_colors=False,
            )
        mock_print.assert_called()

        # check that none of the colors are being used
        colors = ("black", "cyan", "orange3")
        for call in mock_print.call_args_list:
            for color in colors:
                assert color not in call.args[0]

    def test_from_file(self, simple, tmp_path, capsys):
        f_name = tmp_path / "estimator.skops"
        sio.dump(simple, f_name)
        sio.visualize(f_name)

        expected = [
            "root: sklearn.preprocessing._data.MinMaxScaler",
            "└── attrs: builtins.dict",
            "    ├── feature_range: builtins.tuple",
            "    │   ├── content: json-type(-555)",
            "    │   └── content: json-type(123)",
            "    ├── copy: json-type(true)",
            "    ├── clip: json-type(false)",
            '    └── _sklearn_version: json-type("{}")'.format(sklearn.__version__),
        ]
        stdout, _ = capsys.readouterr()
        assert stdout.strip() == "\n".join(expected)

    def test_long_bytes(self, capsys):
        obj = {
            "short_byte": b"abc",
            "long_byte": b"010203040506070809101112131415",
            "short_bytearray": bytearray(b"abc"),
            "long_bytearray": bytearray(b"010203040506070809101112131415"),
        }
        dumped = sio.dumps(obj)
        sio.visualize(dumped)

        expected = [
            "root: builtins.dict",
            "├── short_byte: b'abc'",
            "├── long_byte: b'01020304050...9101112131415'",
            "├── short_bytearray: bytearray(b'abc')",
            "└── long_bytearray: bytearray(b'01020304050...9101112131415')",
        ]
        stdout, _ = capsys.readouterr()
        assert stdout.strip() == "\n".join(expected)
