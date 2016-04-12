#!/usr/bin/env python
# -*- coding: utf-8 -*-

# kb_builder builts keyboard plate and case CAD files using JSON input.
#
# Copyright (C) 2015  Will Stevens (swill)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import json
import logging
import math

import config

log = logging.getLogger()

import FreeCAD
import cadquery
import importDXF
import importSVG
import Mesh
import Part

SWITCH_LAYER = 'switch'
REINFORCING_LAYER = 'reinforcing'
TOP_LAYER = 'top'
BOTTOM_LAYER = 'bottom'
SIMPLE_LAYER = 'simple'
CLOSED_LAYER = 'closed'
OPEN_LAYER = 'open'


class Plate(object):
    def __init__(self, keyboard_layout, export_basename, kerf=0.0,
                 case_type=None, corner_type='round', width_padding=0,
                 height_padding=0, usb_inner_width=10, usb_outer_width=10,
                 usb_height=7.5, stab_type='cherry', corners=0,
                 switch_type='mx', usb_offset=0, pcb_height_padding=0,
                 pcb_width_padding=0, mount_holes_num=0, mount_holes_size=0,
                 thickness=1.5, holes=None, reinforcing=False, oversize=None,
                 oversize_distance=4):
        # User settable things
        self.export_basename = export_basename
        self.case = {'type': case_type}
        self.corner_type = corner_type
        self.corners = corners
        self.kerf = kerf / 2
        self.keyboard_layout = keyboard_layout
        self.holes = holes if holes else []
        self.oversize = oversize if oversize else []
        self.oversize_distance = oversize_distance
        self.stab_type = stab_type
        self.switch_type = switch_type
        self.thickness = thickness
        self.usb_inner_width = usb_inner_width
        self.usb_outer_width = usb_outer_width
        self.usb_height = usb_height
        self.usb_offset = usb_offset
        self.x_pad = width_padding
        self.x_pcb_pad = pcb_width_padding / 2
        self.y_pad = height_padding
        self.y_pcb_pad = pcb_height_padding / 2

        # Sanity checks
        if switch_type not in ('mx', 'alpsmx', 'mx-open', 'mx-open-rotatable', 'alps'):
            log.error('Unknown switch type %s, defaulting to mx!', switch_type)
            self.switch_type = 'mx'

        if stab_type not in ('cherry', 'costar', 'cherry-costar', 'matias', 'alps'):
            log.error('Unknown stabilizer type %s, defaulting to "cherry"!', stab_type)
            self.stab_type = 'cherry'

        # Plate state info
        self.UOM = "mm"
        self.exports = {}
        self.grow_y = 0
        self.grow_x = 0
        self.height = 0
        self.layers = [SWITCH_LAYER]
        self.layout = []
        self.origin = (0,0)
        self.width = 0
        self.x_off = 0

        # Constants
        self.u1 = 19.05
        self.stabs = {
            2: (11.95, 0),       # 2 unit
            3: (19.05, 0),       # 3 unit
            4: (28.575, 0),      # 4 unit
            4.5: (34.671, 0),      # 4.5 unit
            5.5: (42.8625, 0),     # 5.5 unit
            6: (47.625, 9.525),  # 6 unit
            6.25: (50, 0),          # 6.25 unit
            6.5: (52.38, 0),       # 6.5 unit
            7: (57.15, 0),       # 7 unit
            8: (66.675, 0),      # 8 unit
            9: (66.675, 0),      # 9 unit
            10: (66.675, 0)       # 10 unit
        }

        # Initialize the case
        if self.case['type'] == 'poker':
            if mount_holes_size > 0:
                self.case = {'type': 'poker', 'hole_diameter': mount_holes_size}
        elif self.case['type'] == 'sandwich':
            self.layers += [TOP_LAYER, REINFORCING_LAYER, OPEN_LAYER, CLOSED_LAYER, SIMPLE_LAYER, BOTTOM_LAYER]
            if mount_holes_num > 0 and mount_holes_size > 0:
                self.case = {'type': 'sandwich', 'holes': mount_holes_num, 'hole_diameter': mount_holes_size, 'x_holes':0, 'y_holes':0}
        elif self.case['type']:
            log.error('Unknown case type: %s, skipping case generation', self.case['type'])

        if reinforcing:
            self.layers += [REINFORCING_LAYER]

        # Determine the size of each key
        self.parse_layout()

    def create_simple_bottom_layer(self, oversize=0):
        """Returns a copy of the simple bottom layer ready to export.
        """
        p = self.init_plate(oversize=oversize)
        p = p.center(self.width/2 + self.kerf, self.height/2 + self.kerf) # move to center of the plate
        p = p.polyline([(self.width,0), (self.width,1), (self.width+1,1), (self.width,0)]).cutThruAll() # Stupid hack I don't understand.
                                                                                                        # Without this I get:
                                                                                                        # TypeError: must be Part.Shape, not Base.Vector

        return p

    def create_bottom_layer(self, oversize=0):
        """Returns a copy of the bottom layer ready to export.
        """
        p = self.create_simple_bottom_layer(oversize=oversize)
        p = self.cut_usb_hole(p, oversize=oversize)
        points = [
            (self.usb_inner_width/2+self.usb_offset-self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (-self.usb_inner_width/2+self.usb_offset+self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (-self.usb_inner_width/2+self.usb_offset+self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf+self.usb_height),
            (self.usb_inner_width/2+self.usb_offset-self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf+self.usb_height),
            (self.usb_inner_width/2+self.usb_offset-self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf)
        ]
        p = p.polyline(points).cutThruAll()

        return p

    def create_closed_layer(self, oversize=0):
        """Returns a copy of the closed layer ready to export.
        """
        p = self.create_simple_bottom_layer(oversize=oversize)
        points = [
            (-self.width/2+self.x_pad+self.kerf*2, -self.height/2+self.y_pad+self.kerf*2),
            (self.width/2-self.x_pad-self.kerf*2, -self.height/2+self.y_pad+self.kerf*2),
            (self.width/2-self.x_pad-self.kerf*2, self.height/2-self.y_pad-self.kerf*2),
            (-self.width/2+self.x_pad+self.kerf*2, self.height/2-self.y_pad-self.kerf*2),
            (-self.width/2+self.x_pad+self.kerf*2, -self.height/2+self.y_pad+self.kerf*2)
        ]
        p = p.polyline(points).cutThruAll()

        return p

    def create_open_layer(self, oversize=0):
        """Returns a copy of the open layer ready to export.
        """
        p = self.create_closed_layer(oversize=oversize)
        p = self.cut_usb_hole(p, oversize=oversize)

        return p

    def create_switch_layer(self, layer):
        """Returns a copy of one of the switch based layers ready to export.

        The switch based layers are SWITCH_LAYER, REINFORCING_LAYER, and TOP_LAYER.
        """
        prev_width = None
        prev_y_off = 0
        p = self.init_plate(oversize=self.oversize_distance if layer in self.oversize else 0)

        if layer != TOP_LAYER:
            # Put holes into switch/reinforcing plates
            p = self.cut_switch_plate_holes(p)

        for r, row in enumerate(self.layout):
            for k, key in enumerate(row):
                x, y, kx = 0, 0, 0
                if 'x' in key:
                    x = key['x']*self.u1
                    kx = x

                if 'y' in key and k == 0:
                    y = key['y']*self.u1

                if r == 0 and k == 0: # handle placement of the first key in first row
                    p = self.center(p, key['w'] * self.u1 / 2, self.u1 / 2)
                    x += (self.x_pad+self.x_pcb_pad)
                    y += (self.y_pad+self.y_pcb_pad)
                    # set x_off negative since the 'cut_switch' will append 'x' and we need to account for initial spacing
                    self.x_off = -(x - (self.u1/2 + key['w']*self.u1/2) - kx)
                elif k == 0: # handle changing rows
                    p = self.center(p, -self.x_off, self.u1) # move to the next row
                    self.x_off = 0 # reset back to the left side of the plate
                    x += self.u1/2 + key['w']*self.u1/2
                else: # handle all other keys
                    x += prev_width*self.u1/2 + key['w']*self.u1/2

                if prev_y_off != 0: # prev_y_off != 0
                    y += -prev_y_off
                    prev_y_off = 0

                if 'h' in key and key['h'] > 1: # deal with vertical keys
                    prev_y_off = key['h']*self.u1/2 - self.u1/2
                    y += prev_y_off

                # Cut the switch hole
                p = self.cut_switch(p, (x, y), key, layer)
                prev_width = key['w']

        return p

    def cut_usb_hole(self, p, oversize=0):
        """Cut the opening that allows for the USB hole. Assumes the drawing is already centered.
        """
        p = p.center(0, -self.height/2+(self.y_pad+self.y_pcb_pad)/2+self.kerf) # Move up to where the USB connector will be.
        points = [
            (-self.usb_outer_width/2+self.usb_offset+self.kerf, -(self.y_pad+self.y_pcb_pad)/2-oversize/2-self.kerf),
            (self.usb_outer_width/2+self.usb_offset-self.kerf, -(self.y_pad+self.y_pcb_pad)/2-oversize/2-self.kerf),
            (self.usb_inner_width/2+self.usb_offset-self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (-self.usb_inner_width/2+self.usb_offset+self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (self.usb_inner_width/2+self.usb_offset-self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (-self.usb_inner_width/2+self.usb_offset+self.kerf, (self.y_pad+self.y_pcb_pad)/2+self.kerf),
            (-self.usb_outer_width/2+self.usb_offset+self.kerf, -(self.y_pad+self.y_pcb_pad)/2-oversize/2-self.kerf)
        ]
        p = p.polyline(points).cutThruAll()

        return p

    def cut_switch_plate_holes(self, p):
        """Cut any holes specified for the switch/reinforcing plate.
        """
        for hole in self.holes:
            x, y, diameter = hole
            p.center(x, y).hole(diameter).center(-x,-y)
            #p = p.center(x, y).circle(diameter/2).cutThruAll()
            #p = p.center(-x, -y)

        return p

    def draw(self, result):
        """Create and export all our layers.
        """
        result['width'] = self.width
        result['height'] = self.height

        # Create the shape based layers
        layers = (
            (SIMPLE_LAYER, self.create_simple_bottom_layer),
            (BOTTOM_LAYER, self.create_bottom_layer),
            (CLOSED_LAYER, self.create_closed_layer),
            (OPEN_LAYER, self.create_open_layer),
        )
        for layer, create_layer in layers:
            if layer in self.layers:
                p = create_layer(oversize=self.oversize_distance if layer in self.oversize else 0)
                self.export(p, result, layer)

        # Create the switch based layers
        for layer in (SWITCH_LAYER, REINFORCING_LAYER, TOP_LAYER):
            if layer in self.layers:
                p = self.create_switch_layer(layer)
                self.export(p, result, layer)

        return result

    def parse_layout(self):
        """Parse the supplied layout to determine size and populate the properties of each key
        """
        layout_width = 0
        layout_height = 0
        key_desc = False # track if current is not a key and only describes the next key
        for row in self.keyboard_layout:
            if isinstance(row, list): # only handle arrays of keys
                row_width = 0
                row_height = 0
                row_layout = []
                for k in row:
                    key = {}
                    if isinstance(k, dict): # descibes the next key
                        key = k
                        if 'w' not in key:
                            key['w'] = 1
                        if 'h' not in key:
                            key['h'] = 1
                        row_layout.append(key)
                        key_desc = True
                    else: # is just a standard key (we know its a single unit key)
                        if not key_desc: # only handle if it was not already handled as a key_desc
                            key['w'] = 1
                            key['h'] = 1
                            row_layout.append(key)
                        key_desc = False
                    if 'w' in key:
                        row_width += key['w']
                    if 'x' in key:
                        row_width += key['x'] # offsets count towards total row width
                    if 'y' in key:
                        row_height = key['y']
                self.layout.append(row_layout)
                if row_width > layout_width:
                    layout_width = row_width
                layout_height += self.u1 + row_height*self.u1;
            # hidden global features
            if isinstance(row, dict):
                if 'grow_y' in row and (type(row['grow_y']) == int or type(row['grow_y']) == float):
                    self.grow_y = row['grow_y']/2
                if 'grow_x' in row and (type(row['grow_x']) == int or type(row['grow_x']) == float):
                    self.grow_x = row['grow_x']/2
        self.width = layout_width*self.u1 + 2*(self.x_pad+self.x_pcb_pad) + 2*self.kerf
        self.height = layout_height + 2*(self.y_pad+self.y_pcb_pad) + 2*self.kerf
        self.horizontal_edge = self.width / 2
        self.vertical_edge = self.height / 2

    def init_plate(self, oversize=0):
        """Return a basic plate with the features that are common to all layers.

        If oversize is greater than 0 the layer will be made that many mm larger than default, while keeping screws in the same position.

        """
        p = cadquery.Workplane("front").box(self.width+oversize, self.height+oversize, self.thickness)

        # Cut the corners if necessary
        if self.corners > 0 and self.corner_type == 'round':
            p = p.edges("|Z").fillet(self.corners)

        p = p.faces("<Z").workplane()

        if self.corners > 0:
            if self.corner_type == 'beveled':
                # Lower right corner
                points = (
                    (self.horizontal_edge, self.vertical_edge - self.corners), (self.horizontal_edge, self.vertical_edge),
                    (self.horizontal_edge - self.corners, self.vertical_edge), (self.horizontal_edge, self.vertical_edge - self.corners),
                )
                p = p.polyline(points).cutThruAll()
                # Lower left corner
                points = (
                    (-self.horizontal_edge, self.vertical_edge - self.corners), (-self.horizontal_edge, self.vertical_edge),
                    (-self.horizontal_edge + self.corners, self.vertical_edge), (-self.horizontal_edge, self.vertical_edge - self.corners),
                )
                p = p.polyline(points).cutThruAll()
                # Upper right corner
                points = (
                    (self.horizontal_edge, -self.vertical_edge + self.corners), (self.horizontal_edge, -self.vertical_edge),
                    (self.horizontal_edge - self.corners, -self.vertical_edge), (self.horizontal_edge, -self.vertical_edge + self.corners),
                )
                p = p.polyline(points).cutThruAll()
                # Upper left corner
                points = (
                    (-self.horizontal_edge, -self.vertical_edge + self.corners), (-self.horizontal_edge, -self.vertical_edge),
                    (-self.horizontal_edge + self.corners, -self.vertical_edge), (-self.horizontal_edge, -self.vertical_edge + self.corners),
                )
                p = p.polyline(points).cutThruAll()
            elif self.corner_type != 'round':
                log.error('Unknown corner type %s!', self.corner_type)

        # Cut the mount holes in the plate
        if self.case['type'] == 'poker':
            hole_points = [(-139,9.2), (-117.3,-19.4), (-14.3,0), (48,37.9), (117.55,-19.4), (139,9.2)] # holes
            rect_center = (self.width/2) - (3.5/2)
            rect_points = [(rect_center,9.2), (-rect_center,9.2)] # edge slots
            rect_size = (3.5, 5) # edge slot cutout to edge
            for c in hole_points:
                p = p.center(c[0], c[1]).hole(self.case['hole_diameter']).center(-c[0],-c[1])
            for c in rect_points:
                p = p.center(c[0], c[1]).rect(rect_size[0], rect_size[1]).center(-c[0],-c[1])
            p = self.center(p, -self.width/2 + self.kerf, -self.height/2 + self.kerf) # move to top left of the plate
        elif self.case['type'] == 'sandwich':
            p = self.center(p, -self.width/2 + self.kerf, -self.height/2 + self.kerf) # move to top left of the plate
            if 'holes' in self.case and self.case['holes'] >= 4 and 'x_holes' in self.case and 'y_holes' in self.case:
                self.layout_sandwich_holes()
                radius = self.case['hole_diameter']/2 - self.kerf
                x_gap = (self.width - 2*self.case['hole_diameter'] - 2*self.kerf + 1)/(self.case['x_holes'] + 1)
                y_gap = (self.height - 2*self.case['hole_diameter'] - 2*self.kerf + 1)/(self.case['y_holes'] + 1)
                p = p.center(self.case['hole_diameter']-.5, self.case['hole_diameter']-.5)
                for i in range(self.case['x_holes'] + 1):
                    p = p.center(x_gap,0).circle(radius).cutThruAll()
                for i in range(self.case['y_holes'] + 1):
                    p = p.center(0,y_gap).circle(radius).cutThruAll()
                for i in range(self.case['x_holes'] + 1):
                    p = p.center(-x_gap,0).circle(radius).cutThruAll()
                for i in range(self.case['y_holes'] + 1):
                    p = p.center(0,-y_gap).circle(radius).cutThruAll()
                p.center(-self.case['hole_diameter']+.5, -self.case['hole_diameter']+.5)
        elif not self.case['type'] or self.case['type'] == 'reinforcing':
            p = self.center(p, -self.width/2 + self.kerf, -self.height/2 + self.kerf) # move to top left of the plate
        else:
            log.error('Unknown case type: %s', self.case['type'])

        return p

    def layout_sandwich_holes(self):
        """Determine where screw holes should be placed.
        """
        if 'holes' in self.case and self.case['holes'] >= 4 and 'x_holes' in self.case and 'y_holes' in self.case:
            holes = int(self.case['holes'])
            if holes % 2 == 0 and holes >= 4: # holes needs to be even and the first 4 are put in the corners
                x = self.width - self.kerf # x length to split
                y = self.height - self.kerf # y length to split
                _x = 0 # number of holes on each x side (not counting the corner holes)
                _y = 0 # number of holes on each y side (not counting the corner holes)
                free = (holes-4)/2 # number of free holes to be placed on either x or y sides
                for f in range(free): # loop through the available holes and place them
                    if x/(_x+1) == y/(_y+1): # if equal, add the hole to the longer side
                        if x >= y:
                            _x += 1
                        else:
                            _y += 1
                    elif x/(_x+1) > y/(_y+1):
                        _x += 1
                    else:
                        _y += 1
                self.case['x_holes'] = _x
                self.case['y_holes'] = _y

    def rotate_points(self, points, radians, rotate_point):
        """Rotate a sequence of points.

        points: the points to rotate

        radians: the number of degrees to rotate

        rotate_point: the coordinate to rotate around
        """
        def calculate_points(point):
            return (
                math.cos(math.radians(radians)) * (point[0]-rotate_point[0]) - math.sin(math.radians(radians)) * (point[1]-rotate_point[1]) + rotate_point[0],
                math.sin(math.radians(radians)) * (point[0]-rotate_point[0]) + math.cos(math.radians(radians)) * (point[1]-rotate_point[1]) + rotate_point[1]
            )

        return map(calculate_points, points)

    def cut_switch(self, plate, switch_coord, key=None, layer=SWITCH_LAYER):
        """Cut a switch opening

        plate: The plate object

        switch_coord: Center of the switch

        key: A dictionary describing this key, if not provided a 1u key at 0,0 will be used.

        layer: The layer we're cutting
        """
        if not key:
            key = {}

        width = int(key['w']) if 'w' in key else 1
        height = int(key['h']) if 'h' in key else 1
        switch_type = key['_t'] if '_t' in key else self.switch_type
        stab_type = key['_s'] if '_s' in key else self.stab_type
        kerf = key['_k']/2 if '_k' in key else self.kerf
        rotate_key = key['_r'] if '_r' in key else None
        rotate_stab = key['_rs'] if '_rs' in key else None
        center_offset = key['_co'] if '_co' in key else False

        # cut switch cutout
        rotate = None
        if 'h' in key and height > width:
            rotate = True
        points = []

        # Standard locations with no offset
        mx_height = 7
        mx_width = 7
        alps_height = 6.4
        alps_width = 7.8
        wing_inside = 2.9
        wing_outside = 6
        # FIXME: Use more descriptive names here
        mx_stab_inside_y = 4.73
        mx_stab_inside_x = 8.575
        stab_cherry_top_x = 5.53
        stab_4 = 10.3
        stab_5 = 6.45
        stab_6 = 13.6
        mx_stab_outside_x = 15.225
        stab_y_wire = 2.3
        stab_bottom_y_wire = stab_y_wire
        stab_9 = 16.1
        stab_cherry_wing_bottom_x = 0.5
        stab_cherry_bottom_x = 6.77
        stab_12 = 7.75
        stab_13 = 5.97
        stab_cherry_bottom_wing_bottom_y = 7.97
        stab_cherry_half_width = 3.325
        stab_cherry_bottom_wing_half_width = 1.65
        stab_cherry_outside_x = 4.2
        alps_stab_top_y = 4
        alps_stab_bottom_y = 9
        alps_stab_inside_x = 16.7 if width == 2.75 else 12.7
        alps_stab_ouside_x = alps_stab_inside_x + 2.7

        if layer is TOP_LAYER:
            # Cut out openings the size of keycaps
            switch_type = 'mx'
            stab_type = 'cherry'
            if height > 1:
                mx_width = ((self.u1/2) * height) + 0.5
            else:
                mx_width = ((self.u1/2) * width) + 0.5
            mx_height = (self.u1/2) + 0.5
        elif layer is REINFORCING_LAYER:
            offset = 1
            mx_height += offset
            mx_width += offset
            alps_height += offset
            alps_width += offset
            wing_inside += offset
            wing_outside += offset
            mx_stab_inside_x += offset
            stab_cherry_top_x += 2.5 if offset < 2.5 else offset
            stab_y_wire = mx_stab_inside_y = stab_cherry_top_x
            stab_4 += offset
            stab_5 += offset
            stab_6 += offset
            mx_stab_outside_x += offset
            stab_9 += offset
            stab_cherry_bottom_x += 4.28 if offset < 4.28 else offset
            stab_cherry_wing_bottom_x = stab_bottom_y_wire = stab_cherry_bottom_wing_bottom_y = stab_13 = stab_12 = stab_cherry_bottom_x
            stab_cherry_half_width += offset
            stab_cherry_bottom_wing_half_width += offset
            stab_cherry_outside_x += offset
            alps_stab_top_y -= offset
            alps_stab_bottom_y += offset
            alps_stab_inside_x -= offset
            alps_stab_ouside_x += offset

        # Figure out our stabs now in case the switch itself is offset.
        length = width
        if rotate:
            length = height

        if length >= 2:
            x = self.stabs[length][0] if length in self.stabs else self.stabs[2][0]
            if not center_offset:
                center_offset = self.stabs[length][1] if length in self.stabs else self.stabs[2][1]

        if center_offset > 0:
            # If the user has specified an offset stab (EG, 6U) we first move
            # to cut the offset switch hole, and later will move back to cut
            # the stabilizer.
            plate = plate.center(center_offset, 0)

        if switch_type == 'mx':
            points = [
                (mx_width-kerf+self.grow_x,-mx_height+kerf-self.grow_y),
                (mx_width-kerf+self.grow_x,mx_height-kerf+self.grow_y),
                (-mx_width+kerf-self.grow_x,mx_height-kerf+self.grow_y),
                (-mx_width+kerf-self.grow_x,-mx_height+kerf-self.grow_y),
                (mx_width-kerf+self.grow_x,-mx_height+kerf-self.grow_y)
            ]
        elif switch_type == 'alpsmx':
            points = [
                (mx_width-kerf,-mx_height+kerf),
                (mx_width-kerf,-alps_height+kerf),
                (alps_width-kerf,-alps_height+kerf),
                (alps_width-kerf,alps_height-kerf),
                (mx_width-kerf,alps_height-kerf),
                (mx_width-kerf,mx_height-kerf),
                (-mx_width+kerf,mx_height-kerf),
                (-mx_width+kerf,alps_height-kerf),
                (-alps_width+kerf,alps_height-kerf),
                (-alps_width+kerf,-alps_height+kerf),
                (-mx_width+kerf,-alps_height+kerf),
                (-mx_width+kerf,-mx_height+kerf),
                (mx_width-kerf,-mx_height+kerf)
            ]
        elif switch_type == 'mx-open':
            points = [
                (mx_width-kerf,-mx_height+kerf),
                (mx_width-kerf,-wing_outside+kerf),
                (alps_width-kerf,-wing_outside+kerf),
                (alps_width-kerf,-wing_inside-kerf),
                (mx_width-kerf,-wing_inside-kerf),
                (mx_width-kerf,wing_inside+kerf),
                (alps_width-kerf,wing_inside+kerf),
                (alps_width-kerf,wing_outside-kerf),
                (mx_width-kerf,wing_outside-kerf),
                (mx_width-kerf,mx_height-kerf),
                (-mx_width+kerf,mx_height-kerf),
                (-mx_width+kerf,wing_outside-kerf),
                (-alps_width+kerf,wing_outside-kerf),
                (-alps_width+kerf,wing_inside+kerf),
                (-mx_width+kerf,wing_inside+kerf),
                (-mx_width+kerf,-wing_inside-kerf),
                (-alps_width+kerf,-wing_inside-kerf),
                (-alps_width+kerf,-wing_outside+kerf),
                (-mx_width+kerf,-wing_outside+kerf),
                (-mx_width+kerf,-mx_height+kerf),
                (mx_width-kerf,-mx_height+kerf)
            ]
        elif switch_type == 'mx-open-rotatable':
            points = [
                (mx_width-kerf,-mx_height+kerf),
                (mx_width-kerf,-wing_outside+kerf),
                (alps_width-kerf,-wing_outside+kerf),
                (alps_width-kerf,-wing_inside-kerf),
                (mx_width-kerf,-wing_inside-kerf),
                (mx_width-kerf,wing_inside+kerf),
                (alps_width-kerf,wing_inside+kerf),
                (alps_width-kerf,wing_outside-kerf),
                (mx_width-kerf,wing_outside-kerf),
                (mx_width-kerf,mx_height-kerf),
                (wing_outside-kerf,mx_height-kerf),
                (wing_outside-kerf,alps_width-kerf),
                (wing_inside+kerf,alps_width-kerf),
                (wing_inside+kerf,mx_height-kerf),
                (-wing_inside-kerf,mx_height-kerf),
                (-wing_inside-kerf,alps_width-kerf),
                (-wing_outside+kerf,alps_width-kerf),
                (-wing_outside+kerf,mx_height-kerf),
                (-mx_width+kerf,mx_height-kerf),
                (-mx_width+kerf,wing_outside-kerf),
                (-alps_width+kerf,wing_outside-kerf),
                (-alps_width+kerf,wing_inside+kerf),
                (-mx_width+kerf,wing_inside+kerf),
                (-mx_width+kerf,-wing_inside-kerf),
                (-alps_width+kerf,-wing_inside-kerf),
                (-alps_width+kerf,-wing_outside+kerf),
                (-mx_width+kerf,-wing_outside+kerf),
                (-mx_width+kerf,-mx_height+kerf),
                (-wing_outside+kerf,-mx_height+kerf),
                (-wing_outside+kerf,-alps_width+kerf),
                (-wing_inside-kerf,-alps_width+kerf),
                (-wing_inside-kerf,-mx_height+kerf),
                (wing_inside+kerf,-mx_height+kerf),
                (wing_inside+kerf,-alps_width+kerf),
                (wing_outside-kerf,-alps_width+kerf),
                (wing_outside-kerf,-mx_height+kerf),
                (mx_width-kerf,-mx_height+kerf)
            ]
        elif switch_type == 'alps':
            points = [
                (alps_width-kerf,-alps_height+kerf),
                (alps_width-kerf,alps_height-kerf),
                (-alps_width+kerf,alps_height-kerf),
                (-alps_width+kerf,-alps_height+kerf),
                (alps_width-kerf,-alps_height+kerf),
            ]

        if rotate:
            points = self.rotate_points(points, 90, (0,0))
        if rotate_key:
            points = self.rotate_points(points, rotate_key, (0,0))

        plate = self.center(plate, switch_coord[0], switch_coord[1]).polyline(points).cutThruAll()

        if center_offset > 0:
            # Move back to the center of the key/stabilizer
            plate = plate.center(-center_offset, 0)

        # Cut stabilizers. We have different sections for 2U vs other sizes
        # because cherry 2U stabs are shaped differently from larger stabs.
        # This should be refactored for better readability.
        if layer == TOP_LAYER:
            # Don't cut stabs on TOP_LAYER
            self.x_off += switch_coord[0]
            return plate

        elif (width >= 2 and width < 3) or (rotate and height >= 2 and height < 3):
            # Cut 2 unit stabilizer cutout
            if stab_type == 'cherry-costar':
                points = [
                    (mx_width-kerf,-mx_height+kerf),
                    (mx_width-kerf,-mx_stab_inside_y+kerf),
                    (mx_stab_inside_x+kerf,-mx_stab_inside_y+kerf),
                    (mx_stab_inside_x+kerf,-stab_cherry_top_x+kerf),
                    (stab_4+kerf,-stab_cherry_top_x+kerf),
                    (stab_4+kerf,-stab_5+kerf),
                    (stab_6-kerf,-stab_5+kerf),
                    (stab_6-kerf,-stab_cherry_top_x+kerf),
                    (mx_stab_outside_x-kerf,-stab_cherry_top_x+kerf),
                    (mx_stab_outside_x-kerf,-stab_y_wire+kerf),
                    (stab_9-kerf,-stab_y_wire+kerf),
                    (stab_9-kerf,stab_cherry_wing_bottom_x-kerf),
                    (mx_stab_outside_x-kerf,stab_cherry_wing_bottom_x-kerf),
                    (mx_stab_outside_x-kerf,stab_cherry_bottom_x-kerf),
                    (stab_6-kerf,stab_cherry_bottom_x-kerf),
                    (stab_6-kerf,stab_12-kerf),
                    (stab_4+kerf,stab_12-kerf),
                    (stab_4+kerf,stab_cherry_bottom_x-kerf),
                    (mx_stab_inside_x+kerf,stab_cherry_bottom_x-kerf),
                    (mx_stab_inside_x+kerf,stab_13-kerf),
                    (mx_width-kerf,stab_13-kerf),
                    (mx_width-kerf,mx_height-kerf),
                    (-mx_width+kerf,mx_height-kerf),
                    (-mx_width+kerf,stab_13-kerf),
                    (-mx_stab_inside_x-kerf,stab_13-kerf),
                    (-mx_stab_inside_x-kerf,stab_cherry_bottom_x-kerf),
                    (-stab_4-kerf,stab_cherry_bottom_x-kerf),
                    (-stab_4-kerf,stab_12-kerf),
                    (-stab_6+kerf,stab_12-kerf),
                    (-stab_6+kerf,stab_cherry_bottom_x-kerf),
                    (-mx_stab_outside_x+kerf,stab_cherry_bottom_x-kerf),
                    (-mx_stab_outside_x+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-stab_9+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-stab_9+kerf,-stab_y_wire+kerf),
                    (-mx_stab_outside_x+kerf,-stab_y_wire+kerf),
                    (-mx_stab_outside_x+kerf,-stab_cherry_top_x+kerf),
                    (-stab_6+kerf,-stab_cherry_top_x+kerf),
                    (-stab_6+kerf,-stab_5+kerf),
                    (-stab_4-kerf,-stab_5+kerf),
                    (-stab_4-kerf,-stab_cherry_top_x+kerf),
                    (-mx_stab_inside_x-kerf,-stab_cherry_top_x+kerf),
                    (-mx_stab_inside_x-kerf,-mx_stab_inside_y+kerf),
                    (-mx_width+kerf,-mx_stab_inside_y+kerf),
                    (-mx_width+kerf,-mx_height+kerf),
                    (mx_width-kerf,-mx_height+kerf)
                ]
                if rotate:
                    points = self.rotate_points(points, 90, (0,0))
                if rotate_stab:
                    points = self.rotate_points(points, rotate_stab, (0,0))
                plate = plate.polyline(points).cutThruAll()
            elif stab_type == 'cherry':
                points = [
                    (mx_stab_inside_x+kerf,-mx_stab_inside_y+kerf),
                    (mx_stab_inside_x+kerf,-stab_cherry_top_x+kerf),
                    (mx_stab_outside_x-kerf,-stab_cherry_top_x+kerf),
                    (mx_stab_outside_x-kerf,-stab_y_wire+kerf),
                    (stab_9-kerf,-stab_y_wire+kerf),
                    (stab_9-kerf,stab_cherry_wing_bottom_x-kerf),
                    (mx_stab_outside_x-kerf,stab_cherry_wing_bottom_x-kerf),
                    (mx_stab_outside_x-kerf,stab_cherry_bottom_x-kerf),
                    (stab_6-kerf,stab_cherry_bottom_x-kerf),
                    (stab_6-kerf,stab_cherry_bottom_wing_bottom_y-kerf),
                    (stab_4+kerf,stab_cherry_bottom_wing_bottom_y-kerf),
                    (stab_4+kerf,stab_cherry_bottom_x-kerf),
                    (mx_stab_inside_x+kerf,stab_cherry_bottom_x-kerf),
                    (mx_stab_inside_x+kerf,stab_13-kerf),
                    (-mx_stab_inside_x-kerf,stab_13-kerf),
                    (-mx_stab_inside_x-kerf,stab_cherry_bottom_x-kerf),
                    (-stab_4-kerf,stab_cherry_bottom_x-kerf),
                    (-stab_4-kerf,stab_cherry_bottom_wing_bottom_y-kerf),
                    (-stab_6+kerf,stab_cherry_bottom_wing_bottom_y-kerf),
                    (-stab_6+kerf,stab_cherry_bottom_x-kerf),
                    (-mx_stab_outside_x+kerf,stab_cherry_bottom_x-kerf),
                    (-mx_stab_outside_x+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-stab_9+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-stab_9+kerf,-stab_y_wire+kerf),
                    (-mx_stab_outside_x+kerf,-stab_y_wire+kerf),
                    (-mx_stab_outside_x+kerf,-stab_cherry_top_x+kerf),
                    (-mx_stab_inside_x-kerf,-stab_cherry_top_x+kerf),
                    (-mx_stab_inside_x-kerf,-mx_stab_inside_y+kerf),
                    (mx_stab_inside_x+kerf,-mx_stab_inside_y+kerf),
                ]
                if rotate:
                    points = self.rotate_points(points, 90, (0,0))
                if rotate_stab:
                    points = self.rotate_points(points, rotate_stab, (0,0))
                plate = plate.polyline(points).cutThruAll()
            elif stab_type == 'costar':
                points_l = [
                    (-stab_4-kerf,-stab_5+kerf),
                    (-stab_6+kerf,-stab_5+kerf),
                    (-stab_6+kerf,stab_12-kerf),
                    (-stab_4-kerf,stab_12-kerf),
                    (-stab_4-kerf,-stab_5+kerf)
                ]
                points_r = [
                    (stab_4+kerf,-stab_5+kerf),
                    (stab_6-kerf,-stab_5+kerf),
                    (stab_6-kerf,stab_12-kerf),
                    (stab_4+kerf,stab_12-kerf),
                    (stab_4+kerf,-stab_5+kerf)
                ]
                if rotate:
                    points_l = self.rotate_points(points_l, 90, (0,0))
                    points_r = self.rotate_points(points_r, 90, (0,0))
                if rotate_stab:
                    points_l = self.rotate_points(points_l, rotate_stab, (0,0))
                    points_r = self.rotate_points(points_r, rotate_stab, (0,0))
                plate = plate.polyline(points_l).cutThruAll()
                plate = plate.polyline(points_r).cutThruAll()
            elif stab_type in ('alps', 'matias'):
                points_r = [
                    (alps_stab_inside_x+kerf, alps_stab_top_y+kerf),
                    (alps_stab_ouside_x-kerf, alps_stab_top_y+kerf),
                    (alps_stab_ouside_x-kerf, alps_stab_bottom_y-kerf),
                    (alps_stab_inside_x+kerf, alps_stab_bottom_y-kerf),
                    (alps_stab_inside_x+kerf, alps_stab_top_y+kerf)
                ]
                points_l = [
                    (-alps_stab_inside_x+kerf, alps_stab_top_y+kerf),
                    (-alps_stab_ouside_x-kerf, alps_stab_top_y+kerf),
                    (-alps_stab_ouside_x-kerf, alps_stab_bottom_y-kerf),
                    (-alps_stab_inside_x+kerf, alps_stab_bottom_y-kerf),
                    (-alps_stab_inside_x+kerf, alps_stab_top_y+kerf)
                ]

                if rotate:
                    points_l = self.rotate_points(points_l, 90, (0,0))
                    points_r = self.rotate_points(points_r, 90, (0,0))
                if rotate_stab:
                    points_l = self.rotate_points(points_l, rotate_stab, (0,0))
                    points_r = self.rotate_points(points_r, rotate_stab, (0,0))
                plate = plate.polyline(points_l).cutThruAll()
                plate = plate.polyline(points_r).cutThruAll()
            else:
                log.error('Unknown stab type %s! No stabilizer cut', stab_type)

        # Cut larger stabilizer cutouts
        elif (width >= 3) or (rotate and height >= 3):
            if stab_type == 'cherry-costar':
                points = [
                    (x-stab_cherry_half_width+kerf,-stab_y_wire+kerf),
                    (x-stab_cherry_half_width+kerf,-stab_cherry_top_x+kerf),
                    (x-stab_cherry_bottom_wing_half_width+kerf,-stab_cherry_top_x+kerf),
                    (x-stab_cherry_bottom_wing_half_width+kerf,-stab_5+kerf),
                    (x+stab_cherry_bottom_wing_half_width-kerf,-stab_5+kerf),
                    (x+stab_cherry_bottom_wing_half_width-kerf,-stab_cherry_top_x+kerf),
                    (x+stab_cherry_half_width-kerf,-stab_cherry_top_x+kerf),
                    (x+stab_cherry_half_width-kerf,-stab_y_wire+kerf),
                    (x+stab_cherry_outside_x-kerf,-stab_y_wire+kerf),
                    (x+stab_cherry_outside_x-kerf,stab_cherry_wing_bottom_x-kerf),
                    (x+stab_cherry_half_width-kerf,stab_cherry_wing_bottom_x-kerf),
                    (x+stab_cherry_half_width-kerf,stab_cherry_bottom_x-kerf),
                    (x+stab_cherry_bottom_wing_half_width-kerf,stab_cherry_bottom_x-kerf),
                    (x+stab_cherry_bottom_wing_half_width-kerf,stab_12-kerf),
                    (x-stab_cherry_bottom_wing_half_width+kerf,stab_12-kerf),
                    (x-stab_cherry_bottom_wing_half_width+kerf,stab_cherry_bottom_x-kerf),
                    (x-stab_cherry_half_width+kerf,stab_cherry_bottom_x-kerf),
                    (x-stab_cherry_half_width+kerf,stab_y_wire-kerf),
                    (-x+stab_cherry_half_width-kerf,stab_y_wire-kerf),
                    (-x+stab_cherry_half_width-kerf,stab_cherry_bottom_x-kerf),
                    (-x+stab_cherry_bottom_wing_half_width-kerf,stab_cherry_bottom_x-kerf),
                    (-x+stab_cherry_bottom_wing_half_width-kerf,stab_12-kerf),
                    (-x-stab_cherry_bottom_wing_half_width+kerf,stab_12-kerf),
                    (-x-stab_cherry_bottom_wing_half_width+kerf,stab_cherry_bottom_x-kerf),
                    (-x-stab_cherry_half_width+kerf,stab_cherry_bottom_x-kerf),
                    (-x-stab_cherry_half_width+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-x-stab_cherry_outside_x+kerf,stab_cherry_wing_bottom_x-kerf),
                    (-x-stab_cherry_outside_x+kerf,-stab_y_wire+kerf),
                    (-x-stab_cherry_half_width+kerf,-stab_y_wire+kerf),
                    (-x-stab_cherry_half_width+kerf,-stab_cherry_top_x+kerf),
                    (-x-stab_cherry_bottom_wing_half_width+kerf,-stab_cherry_top_x+kerf),
                    (-x-stab_cherry_bottom_wing_half_width+kerf,-stab_5+kerf),
                    (-x+stab_cherry_bottom_wing_half_width-kerf,-stab_5+kerf),
                    (-x+stab_cherry_bottom_wing_half_width-kerf,-stab_cherry_top_x+kerf),
                    (-x+stab_cherry_half_width-kerf,-stab_cherry_top_x+kerf),
                    (-x+stab_cherry_half_width-kerf,-stab_y_wire+kerf),
                    (x-stab_cherry_half_width+kerf,-stab_y_wire+kerf)
                ]
                if rotate:
                    points = self.rotate_points(points, 90, (0,0))
                if rotate_stab:
                    points = self.rotate_points(points, rotate_stab, (0,0))
                plate = plate.polyline(points).cutThruAll()
            elif stab_type == 'cherry':
                points = [
                    (x - stab_cherry_half_width + kerf, -stab_y_wire + kerf),#1
                    (x - stab_cherry_half_width + kerf, -stab_cherry_top_x + kerf),#2
                    (x + stab_cherry_half_width - kerf, -stab_cherry_top_x + kerf),#3
                    (x + stab_cherry_half_width - kerf, -stab_y_wire + kerf),#4
                    (x + stab_cherry_outside_x - kerf, -stab_y_wire + kerf),#5
                    (x + stab_cherry_outside_x - kerf, stab_cherry_wing_bottom_x - kerf),#6
                    (x + stab_cherry_half_width - kerf, stab_cherry_wing_bottom_x - kerf),#7
                    (x + stab_cherry_half_width - kerf, stab_cherry_bottom_x - kerf),#8
                    (x + stab_cherry_bottom_wing_half_width - kerf, stab_cherry_bottom_x - kerf),#9
                    (x + stab_cherry_bottom_wing_half_width - kerf, stab_cherry_bottom_wing_bottom_y - kerf),#10
                    (x - stab_cherry_bottom_wing_half_width + kerf, stab_cherry_bottom_wing_bottom_y - kerf),#11
                    (x - stab_cherry_bottom_wing_half_width + kerf, stab_cherry_bottom_x - kerf),#12
                    (x - stab_cherry_half_width + kerf, stab_cherry_bottom_x - kerf),#13
                    (x - stab_cherry_half_width + kerf, stab_bottom_y_wire - kerf),#14
                    (-x + stab_cherry_half_width - kerf, stab_bottom_y_wire - kerf),#15
                    (-x + stab_cherry_half_width - kerf, stab_cherry_bottom_x - kerf),#16
                    (-x + stab_cherry_bottom_wing_half_width - kerf, stab_cherry_bottom_x - kerf),#17
                    (-x + stab_cherry_bottom_wing_half_width - kerf, stab_cherry_bottom_wing_bottom_y - kerf),#18
                    (-x - stab_cherry_bottom_wing_half_width + kerf, stab_cherry_bottom_wing_bottom_y - kerf),#19
                    (-x - stab_cherry_bottom_wing_half_width + kerf, stab_cherry_bottom_x - kerf),#20
                    (-x - stab_cherry_half_width + kerf, stab_cherry_bottom_x - kerf),#21
                    (-x - stab_cherry_half_width + kerf, stab_cherry_wing_bottom_x - kerf),#22
                    (-x - stab_cherry_outside_x + kerf, stab_cherry_wing_bottom_x - kerf),#23
                    (-x - stab_cherry_outside_x + kerf, -stab_y_wire + kerf),#24
                    (-x - stab_cherry_half_width + kerf, -stab_y_wire + kerf),#25
                    (-x - stab_cherry_half_width + kerf, -stab_cherry_top_x + kerf),#26
                    (-x + stab_cherry_half_width - kerf, -stab_cherry_top_x + kerf),#27
                    (-x + stab_cherry_half_width - kerf, -stab_y_wire + kerf),#28
                    (x - stab_cherry_half_width + kerf, -stab_y_wire + kerf),#1
                ]
                if rotate:
                    points = self.rotate_points(points, 90, (0,0))
                if rotate_stab:
                    points = self.rotate_points(points, rotate_stab, (0,0))
                plate = plate.polyline(points).cutThruAll()
            elif stab_type in ('costar', 'matias'):
                points_l = [
                    (-x+stab_cherry_bottom_wing_half_width-kerf,-stab_5+kerf), (-x-stab_cherry_bottom_wing_half_width+kerf,-stab_5+kerf), (-x-stab_cherry_bottom_wing_half_width+kerf,stab_12-kerf),
                    (-x+stab_cherry_bottom_wing_half_width-kerf,stab_12-kerf), (-x+stab_cherry_bottom_wing_half_width-kerf,-stab_5+kerf)
                ]
                points_r = [
                    (x-stab_cherry_bottom_wing_half_width+kerf,-stab_5+kerf), (x+stab_cherry_bottom_wing_half_width-kerf,-stab_5+kerf), (x+stab_cherry_bottom_wing_half_width-kerf,stab_12-kerf),
                    (x-stab_cherry_bottom_wing_half_width+kerf,stab_12-kerf), (x-stab_cherry_bottom_wing_half_width+kerf,-stab_5+kerf)
                ]
                if rotate:
                    points_l = self.rotate_points(points_l, 90, (0,0))
                    points_r = self.rotate_points(points_r, 90, (0,0))
                if rotate_stab:
                    points_l = self.rotate_points(points_l, rotate_stab, (0,0))
                    points_r = self.rotate_points(points_r, rotate_stab, (0,0))
                plate = plate.polyline(points_l).cutThruAll()
                plate = plate.polyline(points_r).cutThruAll()
            elif stab_type == 'alps':
                # FIXME: Pull this in from your stashed patch
                log.error('Vintage alps stabilizers for spacebar not implemented!')
            else:
                log.error('Unknown stab type %s! No stabilizer cut', stab_type)

        self.x_off += switch_coord[0]
        return plate

    def center(self, plate, x, y):
        """Move the center point and record how far we have moved relative to the center of the plate.
        """
        self.origin = (self.origin[0]+x, self.origin[1]+y)

        return plate.center(x, y)

    def __repr__(self):
        """Print out all Plate object configuration settings.
        """
        settings = {}

        settings['plate_layout'] = self.layout
        settings['switch_type'] = self.switch_type
        settings['stabilizer_type'] = self.stab_type
        settings['case_type_and_holes'] = self.case
        settings['width_padding'] = self.x_pad
        settings['height_padding'] = self.y_pad
        settings['pcb_width_padding'] = self.x_pcb_pad
        settings['pcb_height_padding'] = self.y_pcb_pad
        settings['plate_corners'] = self.corners
        settings['kerf'] = self.kerf

        return json.dumps(settings, sort_keys=True, indent=4, separators=(',', ': '))

    def export(self, plate, result, layer):
        """Export the specified layer to the formats specified in result['formats'].
        """
        log.info("Exporting %s layer for %s", layer, self.export_basename)
        # draw the part so we can export it
        Part.show(plate.val().wrapped)
        doc = FreeCAD.ActiveDocument
        # export the drawing into different formats
        pwd_len = len(config.app['pwd']) # the absolute part of the working directory (aka - outside the web space)
        self.exports[layer] = []
        if 'js' in result['formats']:
            with open("%s/%s_%s.js" % (config.app['export'], layer, self.export_basename), "w") as f:
                cadquery.exporters.exportShape(plate, 'TJS', f)
                self.exports[layer].append({'name': 'js', 'url': '%s/%s_%s.js' % (config.app['export'][pwd_len:], layer, self.export_basename)})
                log.info("Exported 'JS'")
        if 'brp' in result['formats']:
            Part.export(doc.Objects, "%s/%s_%s.brp" % (config.app['export'], layer, self.export_basename))
            self.exports[layer].append({'name': 'brp', 'url': '%s/%s_%s.brp' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'BRP'")
        if 'stp' in result['formats']:
            Part.export(doc.Objects, "%s/%s_%s.stp" % (config.app['export'], layer, self.export_basename))
            self.exports[layer].append({'name': 'stp', 'url': '%s/%s_%s.stp' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'STP'")
        if 'stl' in result['formats']:
            Mesh.export(doc.Objects, "%s/%s_%s.stl" % (config.app['export'], layer, self.export_basename))
            self.exports[layer].append({'name': 'stl', 'url': '%s/%s_%s.stl' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'STL'")
        if 'dxf' in result['formats']:
            importDXF.export(doc.Objects, "%s/%s_%s.dxf" % (config.app['export'], layer, self.export_basename))
            self.exports[layer].append({'name': 'dxf', 'url': '%s/%s_%s.dxf' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'DXF'")
        if 'svg' in result['formats']:
            importSVG.export(doc.Objects, "%s/%s_%s.svg" % (config.app['export'], layer, self.export_basename))
            self.exports[layer].append({'name': 'svg', 'url': '%s/%s_%s.svg' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'SVG'")
        if 'json' in result['formats'] and layer == SWITCH_LAYER:
            with open("%s/%s_%s.json" % (config.app['export'], layer, self.export_basename), 'w') as json_file:
                json_file.write(repr(self))
            self.exports[layer].append({'name': 'json', 'url': '%s/%s_%s.json' % (config.app['export'][pwd_len:], layer, self.export_basename)})
            log.info("Exported 'JSON'")
        # remove all the documents from the view before we move on
        for o in doc.Objects:
            doc.removeObject(o.Label)

# take the input from the webserver and instantiate and draw the plate
def build(data):
    # create the result object
    p = Plate(**data)
    result = {
        'formats': config.app['formats'],
        'plates': p.layers,
        'exports': p.exports
    }
    result = p.draw(result)
    log.info("Finished drawing: %s", data['export_basename'])
    return result # return the metadata result to the webserver


