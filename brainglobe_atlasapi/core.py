import warnings
from collections import UserDict
from pathlib import Path

import numpy as np
import pandas as pd
from brainglobe_space import AnatomicalSpace

from brainglobe_atlasapi.descriptors import (
    ANNOTATION_FILENAME,
    HEMISPHERES_FILENAME,
    MESHES_DIRNAME,
    METADATA_FILENAME,
    REFERENCE_FILENAME,
    STRUCTURES_FILENAME,
)
from brainglobe_atlasapi.structure_class import StructuresDict
from brainglobe_atlasapi.utils import read_json, read_tiff, get_leaves_from_tree

LEFT_HEMI_VAL = 1
RIGHT_HEMI_VAL = 2


class Atlas:
    """Base class to handle atlases in BrainGlobe.

    Parameters
    ----------
    path : str or Path object
        Path to folder containing data info.
    """

    def __init__(self, path):
        self.root_dir = Path(path)
        self.metadata = read_json(self.root_dir / METADATA_FILENAME)

        # Load structures list:
        structures_list = read_json(self.root_dir / STRUCTURES_FILENAME)
        # keep to generate tree and dataframe views when necessary
        self.structures_list = structures_list

        # Add entry for file paths:
        for struct in structures_list:
            struct["mesh_filename"] = (
                self.root_dir / MESHES_DIRNAME / "{}.obj".format(struct["id"])
            )

        self.structures = StructuresDict(structures_list)

        # Parse the structure list to find leaf nodes
        self.leaf_nodes = get_leaves_from_tree(self.structures_list)


        # Instantiate SpaceConvention object describing the current atlas:
        self.space = AnatomicalSpace(
            origin=self.orientation,
            shape=self.shape,
            resolution=self.resolution,
        )

        self._reference = None

        try:
            self.additional_references = AdditionalRefDict(
                references_list=self.metadata["additional_references"],
                data_path=self.root_dir,
            )
        except KeyError:
            warnings.warn(
                "This atlas seems to be outdated as no "
                "additional_references list "
                "is found in metadata!"
            )

        self._annotation = None
        self._hemispheres = None
        self._lookup = None

    @property
    def resolution(self):
        """Make resolution more accessible from class."""
        return tuple(self.metadata["resolution"])

    @property
    def orientation(self):
        """Make orientation more accessible from class."""
        return self.metadata["orientation"]

    @property
    def shape(self):
        """Make shape more accessible from class."""
        return tuple(self.metadata["shape"])

    @property
    def shape_um(self):
        """Make shape more accessible from class."""
        return tuple([s * r for s, r in zip(self.shape, self.resolution)])

    @property
    def hierarchy(self):
        """Returns a Treelib.tree object with structures hierarchy."""
        return self.structures.tree

    @property
    def lookup_df(self):
        """Returns a dataframe with id, acronym and name for each structure."""
        if self._lookup is None:
            self._lookup = pd.DataFrame(
                dict(
                    acronym=[r["acronym"] for r in self.structures_list],
                    id=[r["id"] for r in self.structures_list],
                    name=[r["name"] for r in self.structures_list],
                )
            )
        return self._lookup

    @property
    def reference(self):
        if self._reference is None:
            self._reference = read_tiff(self.root_dir / REFERENCE_FILENAME)
        return self._reference

    @property
    def annotation(self):
        if self._annotation is None:
            self._annotation = read_tiff(self.root_dir / ANNOTATION_FILENAME)
        return self._annotation

    @property
    def hemispheres(self):
        if self._hemispheres is None:
            # If reference is symmetric generate hemispheres block:
            if self.metadata["symmetric"]:
                # initialize empty stack:
                stack = np.full(self.metadata["shape"], 2, dtype=np.uint8)

                # Use bgspace description to fill out with hemisphere values:
                front_ax_idx = self.space.axes_order.index("frontal")

                # Fill out with 2s the right hemisphere:
                slices = [slice(None) for _ in range(3)]
                slices[front_ax_idx] = slice(
                    stack.shape[front_ax_idx] // 2 + 1, None
                )
                stack[tuple(slices)] = 1

                self._hemispheres = stack
            else:
                self._hemispheres = read_tiff(
                    self.root_dir / HEMISPHERES_FILENAME
                )
        return self._hemispheres

    def hemisphere_from_coords(self, coords, microns=False, as_string=False):
        """Get the hemisphere from a coordinate triplet.

        Parameters
        ----------
        coords : tuple or list or numpy array
            Triplet of coordinates. Default in voxels, can be microns if
            microns=True
        microns : bool
            If true, coordinates are interpreted in microns.
        as_string : bool
            If true, returns "left" or "right".


        Returns
        -------
        int or string
            Hemisphere label.

        """

        hem = self.hemispheres[self._idx_from_coords(coords, microns)]
        if as_string:
            hem = ["left", "right"][hem - 1]
        return hem

    def structure_from_coords(
        self,
        coords,
        microns=False,
        as_acronym=False,
        hierarchy_lev=None,
        key_error_string="Outside atlas",
    ):
        """Get the structure from a coordinate triplet.

        Parameters
        ----------
        coords : tuple or list or numpy array
            Triplet of coordinates.
        microns : bool
            If true, coordinates are interpreted in microns.
        as_acronym : bool
            If true, the region acronym is returned.
            If outside atlas (structure gives key error),
            return "Outside atlas"
        hierarchy_lev : int or None
            If specified, return parent node at thi hierarchy level.

        Returns
        -------
        int or string
            Structure containing the coordinates.
        """

        rid = self.annotation[self._idx_from_coords(coords, microns)]

        # If we want to cut the result at some high level of the hierarchy:
        if hierarchy_lev is not None:
            rid = self.structures[rid]["structure_id_path"][hierarchy_lev]

        if as_acronym:
            try:
                d = self.structures[rid]
                return d["acronym"]
            except KeyError:
                return key_error_string
        else:
            return rid

    # Meshes-related methods:
    def _get_from_structure(self, structure, key):
        """Internal interface to the structure dict. It support querying with a
        single structure id or a list of ids.

        Parameters
        ----------
        structure : int or str or list
            Valid id or acronym, or list if ids or acronyms.
        key : str
            Key for the Structure dictionary (eg "name" or "rgb_triplet").

        Returns
        -------
        value or list of values
            If structure is a list, returns list.

        """
        if isinstance(structure, list) or isinstance(structure, tuple):
            return [self._get_from_structure(s, key) for s in structure]
        else:
            return self.structures[structure][key]

    def mesh_from_structure(self, structure):
        return self._get_from_structure(structure, "mesh")

    def meshfile_from_structure(self, structure):
        return self._get_from_structure(structure, "mesh_filename")

    def root_mesh(self):
        return self.mesh_from_structure("root")

    def root_meshfile(self):
        return self.meshfile_from_structure("root")

    def _idx_from_coords(self, coords, microns):
        # If microns are passed, convert:
        if microns:
            coords = [c / res for c, res in zip(coords, self.resolution)]

        return tuple([int(c) for c in coords])

    def get_structure_ancestors(self, structure):
        """Returns a list of acronyms for all ancestors of a given structure

        Parameters
        ----------
        structure : str or int
            Structure id or acronym

        Returns
        -------
        list
            List of descendants acronyms

        """
        ancestors_id = self._get_from_structure(
            structure, "structure_id_path"
        )[:-1]

        return self._get_from_structure(ancestors_id, "acronym")

    def get_structure_descendants(self, structure):
        """Returns a list of acronyms for all descendants of a given structure.

        Parameters
        ----------
        structure : str or int
            Structure id or acronym

        Returns
        -------
        list
            List of descendants acronyms

        """
        structure = self._get_from_structure(structure, "acronym")

        # For all structures check if given structure is ancestor
        descendants = []
        for struc in self.structures.keys():
            if structure in self.get_structure_ancestors(struc):
                descendants.append(self._get_from_structure(struc, "acronym"))

        return descendants

    def get_structure_mask(self, structure, hemisphere=0):
        """
        Returns a stack with the mask for a specific structure (including all
        sub-structures).

        This function might take a few seconds for structures with many
        children.

        Parameters
        ----------
        structure : str or int
            Structure id or acronym
        hemisphere : int, optional
            -1 left, 1 right, 0 both, by default = 0

        Returns
        -------
        np.array
            stack containing the mask array.
        """
        structure_id = self.structures[structure]["id"]
        descendants = self.get_structure_descendants(structure)

        descendant_ids = [
            self.structures[descendant]["id"] for descendant in descendants
        ]
        descendant_ids.append(structure_id)

        mask_stack = np.zeros(self.shape, self.annotation.dtype)
        mask_stack[np.isin(self.annotation, descendant_ids)] = structure_id

        if not hemisphere == 0:
            if hemisphere == -1:
                mask_stack[self.hemispheres == RIGHT_HEMI_VAL] = 0
            elif hemisphere == 1:
                mask_stack[self.hemispheres == LEFT_HEMI_VAL] = 0
            else:
                raise ValueError('Hemispheres parameter should be one of [-1, 0, 1]')

        return mask_stack


class AdditionalRefDict(UserDict):
    """Class implementing the lazy loading of secondary references
    if the dictionary is queried for it.
    """

    def __init__(self, references_list, data_path, *args, **kwargs):
        self.data_path = data_path
        self.references_list = references_list

        super().__init__(*args, **kwargs)

    def __getitem__(self, ref_name):
        if ref_name not in self.keys():
            if ref_name not in self.references_list:
                warnings.warn(
                    f"No reference named {ref_name} "
                    f"(available: {self.references_list})"
                )
                return None

            self.data[ref_name] = read_tiff(
                self.data_path / f"{ref_name}.tiff"
            )

        return self.data[ref_name]
