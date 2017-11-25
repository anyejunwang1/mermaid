"""
This implements registration as a command line tool.
Contributors:
  Marc Niethammer: mn@cs.unc.edu
"""

# Note: all images have to be in the format BxCxXxYxZ (BxCxX in 1D and BxCxXxY in 2D)
# I.e., in 1D, 2D, 3D we are dealing with 3D, 4D, 5D tensors. B is the batchsize, and C are the channels
# (for example to support color-images or general multi-modal registration scenarios)

from __future__ import print_function
import set_pyreg_paths

# first do the torch imports
import torch
from torch.autograd import Variable
from time import time

import nrrd
import itk
import numpy as np

import pyreg.module_parameters as pars
import pyreg.utils as utils

def read_images(source_image_name,target_image_name, normalize_spacing = True, normalize_intensities = True):
    I0_itk = itk.imread( source_image_name )
    I1_itk = itk.imread( target_image_name )

    I0,md_I0 = utils.convert_itk_image_to_numpy(I0_itk)
    I1,md_I1 = utils.convert_itk_image_to_numpy(I1_itk)

    I0 = I0.squeeze()
    I1 = I1.squeeze()

    if normalize_intensities:
        print( 'Normalizing image intensities')
        I0 = I0 / np.percentile(I0, 95) * 0.95
        I1 = I1 / np.percentile(I1, 95) * 0.95

    dim0 = I0.ndim
    dim1 = I1.ndim

    assert (dim0 == dim1)
    # TODO: do a better test for equality for the images here

    # introduce first two dimensions as 1, 1
    I0 = utils.transform_image_to_NC_image_format(I0)
    I1 = utils.transform_image_to_NC_image_format(I1)

    if normalize_spacing:
        print( 'Normalizing the spacing' )
        sz = np.array(I0.shape)
        assert (len(sz) == dim0 + 2)
        # spacing so that everything is in [0,1]^2 for now
        spacing = 1. / (sz[2::] - 1)  # the first two dimensions are batch size and number of image channels

    else:
       spacing = utils.compute_squeezed_spacing(md_I0['spacing'],md_I0['dimension'],md_I0['sizes'],dim0)

    print('Spacing = ' + str(spacing))

    return I0, I1, spacing, md_I0, md_I1


def do_registration( I0_name, I1_name, visualize, visualize_step, use_multi_scale, normalize_spacing, normalize_intensities, par_algconf ):

    from pyreg.data_wrapper import AdaptVal
    import pyreg.smoother_factory as SF
    import pyreg.multiscale_optimizer as MO
    from pyreg.config_parser import nr_of_threads

    params = pars.ParameterDict()

    par_image_smoothing = par_algconf['algconf']['image_smoothing']
    par_model = par_algconf['algconf']['model']
    par_optimizer = par_algconf['algconf']['optimizer']

    use_map = par_model['deformation']['use_map']
    model_name = par_model['deformation']['name']

    if use_map:
        model_name = model_name + '_map'
    else:
        model_name = model_name + '_image'

    # general parameters
    params['registration_model'] = par_algconf['algconf']['model']['registration_model']

    torch.set_num_threads( nr_of_threads )
    print('Number of pytorch threads set to: ' + str(torch.get_num_threads()))

    I0, I1, spacing, md_I0, md_I1 = read_images( I0_name, I1_name, normalize_spacing, normalize_intensities )
    sz = I0.shape

    # create the source and target image as pyTorch variables
    ISource = AdaptVal(Variable(torch.from_numpy(I0.copy()), requires_grad=False))
    ITarget = AdaptVal(Variable(torch.from_numpy(I1), requires_grad=False))

    smooth_images = par_image_smoothing['smooth_images']
    if smooth_images:
        # smooth both a little bit
        params['image_smoothing'] = par_algconf['algconf']['image_smoothing']
        cparams = params['image_smoothing']
        s = SF.SmootherFactory(sz[2::], spacing).create_smoother(cparams)
        ISource = s.smooth_scalar_field(ISource)
        ITarget = s.smooth_scalar_field(ITarget)

    if not use_multi_scale:
        # create multi-scale settings for single-scale solution
        multi_scale_scale_factors = [1.0]
        multi_scale_iterations_per_scale = [par_optimizer['single_scale']['nr_of_iterations']]
    else:
        multi_scale_scale_factors = par_optimizer['multi_scale']['scale_factors']
        multi_scale_iterations_per_scale = par_optimizer['multi_scale']['scale_iterations']

    mo = MO.MultiScaleRegistrationOptimizer(sz, spacing, use_map, params)

    optimizer_name = par_optimizer['name']

    mo.set_optimizer_by_name(optimizer_name)
    mo.set_visualization(visualize)
    mo.set_visualize_step(visualize_step)

    mo.set_model(model_name)

    mo.set_source_image(ISource)
    mo.set_target_image(ITarget)

    mo.set_scale_factors(multi_scale_scale_factors)
    mo.set_number_of_iterations_per_scale(multi_scale_iterations_per_scale)

    # and now do the optimization
    mo.optimize()

    optimized_energy = mo.get_energy()
    warped_image = mo.get_warped_image()
    optimized_map = mo.get_map()
    optimized_reg_parameters = mo.get_model_parameters()

    return warped_image, optimized_map, optimized_reg_parameters, optimized_energy, params, md_I0


if __name__ == "__main__":
    # execute this as a script

    import argparse

    parser = argparse.ArgumentParser(description='Registers two images')

    required = parser.add_argument_group('required arguments')
    required.add_argument('--moving_image', required=False, default='../test_data/brain_slices/ws_slice.nrrd', help='Moving image')
    required.add_argument('--target_image', required=False, default='../test_data/brain_slices/wt_slice.nrrd', help='Target image')

    parser.add_argument('--warped_image', required=False, help='Warped image after registration')
    parser.add_argument('--map', required=False, help='Computed map')
    parser.add_argument('--alg_conf', required=False, default='../settings/algconf_settings.json')
    parser.add_argument('--visualize', action='store_false', default=False, help='visualizes the output')
    parser.add_argument('--visualize_step', required=False, default=5, help='Number of iterations between visualization output')
    parser.add_argument('--used_config', default=None, help='Name to write out the used configuration')
    parser.add_argument('--use_multiscale', required=False,default=False, help='Uses multi-scale optimization')
    parser.add_argument('--normalize_spacing', required=False,default=True, help='Normalizes the spacing to [0,1]^d')
    parser.add_argument('--normalize_intensities', required=False, default=True, help='Normalizes the intensities so that the 95th percentile is 0.95')
    parser.add_argument('--write_map', required=False, default=None, help='File to write the resulting map to (if map-based algorithm)')
    parser.add_argument('--write_warped_image', required=False, default=None, help='File to write the warped source image to (if image-based algorithm)')
    parser.add_argument('--write_reg_params', required=False, default=None, help='File to write the optimized registration parameters to')
    args = parser.parse_args()

    # load the specified configuration files
    par_algconf = pars.ParameterDict()
    par_algconf.load_JSON( args.alg_conf )

    moving_image = args.moving_image
    target_image = args.target_image

    visualize = args.visualize
    visualize_step = args.visualize_step
    use_multiscale = args.use_multiscale
    normalize_spacing = args.normalize_spacing
    normalize_intensities = args.normalize_intensities
    used_config = args.used_config
    write_map = args.write_map
    write_warped_image = args.write_warped_image
    write_reg_params = args.write_reg_params

else:
    # load the specified configuration files
    par_algconf = pars.ParameterDict()
    par_algconf.load_JSON('../settings/algconf_settings.json')

    moving_image = '../test_data/brain_slices/ws_slice.nrrd'
    target_image = '../test_data/brain_slices/wt_slice.nrrd'
    visualize = True
    visualize_step = 5
    use_multiscale = False
    normalize_spacing = True
    normalize_intensities = True
    used_config = 'used_config'

    #TODO: Check what happens here when using .nhdr file; there seems to be some confusion in the library
    # where the datafile is not changed
    write_map = 'map_out.nrrd'
    write_warped_image = 'warped_image_out.nrrd'
    write_reg_params = 'reg_params_out.nrrd'

# now do the actual registration
since = time()

warped_image, optimized_map, optimized_reg_parameters, optimized_energy, params, md_I = \
    do_registration( moving_image, target_image, visualize, visualize_step, use_multiscale, normalize_spacing, normalize_intensities, par_algconf )

print('The final energy was: E={energy}, similarityE={similarityE}, regE={regE}'
                  .format(energy=optimized_energy[0],
                          similarityE=optimized_energy[1],
                          regE=optimized_energy[2]))

if write_map is not None:
    if optimized_map is not None:
        om_data = optimized_map.data.numpy()
        nrrd.write( write_map, om_data, md_I )
    else:
        print('Warning: Map cannot be written as it was not computed -- maybe you are using an image-based algorithm?')

if write_warped_image is not None:
    if warped_image is not None:
        wi_data = warped_image.data.numpy()
        nrrd.write(write_warped_image, wi_data, md_I)
    else:
        print('Warning: Warped image cannot be written as it was not computed -- maybe you are using a map-based algorithm?')

if write_reg_params is not None:
    if optimized_reg_parameters is not None:
        rp_data = optimized_reg_parameters.data.numpy()
        nrrd.write(write_reg_params, rp_data, md_I)
    else:
        print('Warning: optimized parameters were not computed and hence cannot be saved.')

if used_config is not None:
    print('Writing the used configuration to file.')
    params.write_JSON( used_config + '_settings_clean.json')
    params.write_JSON_comments( used_config + '_settings_comments.json')

print("time {}".format(time()-since))



