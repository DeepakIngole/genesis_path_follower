#!/usr/bin/env python

# MPC Command Publisher/Controller Module Interface to the Genesis.
# This version uses the Nonlinear Kinematic Bicycle Model.

###########################################
#### ROS
###########################################
import rospy
from genesis_path_follower.msg import state_est
from genesis_path_follower.msg import mpc_path
from std_msgs.msg import UInt8 as UInt8Msg
from std_msgs.msg import Float32 as Float32Msg

###########################################
#### LOAD ROSPARAMS
###########################################
if rospy.has_param("mat_waypoints"):
	mat_fname = rospy.get_param("mat_waypoints")
else:
	raise ValueError("No Matfile of waypoints provided!")

if rospy.has_param("track_using_time") and rospy.has_param("target_vel"):
	track_with_time = rospy.get_param("track_using_time")
	target_vel = rospy.get_param("target_vel")	
else:
	raise ValueError("Invalid rosparam trajectory definition: track_using_time and target_vel")

if rospy.has_param("scripts_dir"):
	scripts_dir = rospy.get_param("scripts_dir")
else:
	raise ValueError("Did not provide the scripts directory!")

if rospy.has_param("lat0") and rospy.has_param("lat0") and rospy.has_param("lat0"):
	lat0 = rospy.get_param("lat0")
	lon0 = rospy.get_param("lon0")
	yaw0 = rospy.get_param("yaw0")
else:
	raise ValueError("Invalid rosparam global origin provided!")
###########################################
#### Reference GPS Trajectory Module
###########################################
# Access Python modules for path processing.  Ugly way of doing it, can seek to clean this up in the future.
import sys
sys.path.append(scripts_dir)

# TODO: remove hard coded horizon and dt -> should be rosparams.
from gps_utils import ref_gps_traj as rgt
grt = rgt.GPSRefTrajectory(mat_filename=mat_fname, LAT0=lat0, LON0=lon0, YAW0=yaw0, traj_horizon=10, traj_dt=0.2)

###########################################
#### MPC Controller Module with Cost Function Weights.
#### Global Variables for Callbacks/Control Loop.
###########################################
from mpc_utils.kinematic_mpc import KinMPCPathFollower
kmpc = KinMPCPathFollower(N=10, DT=0.2, Q =[1., 1., 10., 0.0], R = [10., 100.])

# Reference for MPC
if target_vel > 0.0:
	des_speed = target_vel
else:
	des_speed = 0.00

ref_lock = False				
received_reference = False
x_curr  = 0.0
y_curr  = 0.0
psi_curr  = 0.0
v_curr  = 0.0
command_stop = False

###########################################
#### State Estimation Callback.
###########################################
def state_est_callback(msg):
	global x_curr, y_curr, psi_curr, v_curr
	global received_reference

	if ref_lock == False:
		x_curr = msg.x
		y_curr = msg.y
		psi_curr = msg.psi
		v_curr = msg.v
		received_reference = True

def pub_loop(acc_pub_obj, steer_pub_obj, mpc_path_pub_obj):
	loop_rate = rospy.Rate(50.0)

	# Warm Start Variables: use the previous solution
	u_ws  = None
	z_ws  = None
	sl_ws = None

	while not rospy.is_shutdown():
		if not received_reference:
			# Reference not received so don't use MPC yet.
			loop_rate.sleep()
			continue

		# Ref lock used to ensure that get/set of state doesn't happen simultaneously.
		global ref_lock				
		ref_lock = True

		global x_curr, y_curr, psi_curr, v_curr, des_speed, command_stop

		if not track_with_time:
			# fixed velocity-based path tracking
			x_ref, y_ref, psi_ref, stop_cmd = grt.get_waypoints(x_curr, y_curr, psi_curr, des_speed)
			if stop_cmd == True:
				command_stop = True			
		else:
			# trajectory tracking
			x_ref, y_ref, psi_ref, stop_cmd = grt.get_waypoints(x_curr, y_curr, psi_curr)

			if stop_cmd == True:
				command_stop = True
		
		# Update Model
		kmpc.update_initial_condition(x_curr, y_curr, psi_curr, v_curr)
		kmpc.update_reference(x_ref[1:], y_ref[1:], psi_ref[1:], 10*[des_speed]) # TODO: use parameter horizon

		ref_lock = False
		
		if command_stop == False:
			# a_opt, df_opt, is_opt, solv_time = kmpc.solve_model()
			
			# Use warm start from previous solution.
			is_opt, solve_time, u_opt, z_opt, sl_opt, z_ref = kmpc.solve(z_dv_warm_start = z_ws, u_dv_warm_start = u_ws, sl_dv_warm_start = sl_ws)			

			# Don't warm start.
			# is_opt, solve_time, u_opt, z_opt, sl_opt, z_ref = kmpc.solve()			

			rostm = rospy.get_rostime()
			tm_secs = rostm.secs + 1e-9 * rostm.nsecs

			log_str = "Solve Status: %s, Acc: %.3f, SA: %.3f, ST: %.3f" % (is_opt, u_opt[0,0], u_opt[0,1], solve_time)
			rospy.loginfo(log_str)

			if is_opt:
				acc_pub_obj.publish(Float32Msg(u_opt[0,0]))
				steer_pub_obj.publish(Float32Msg(u_opt[0,1]))

			kmpc.update_previous_input(u_opt[0,0], u_opt[0,1])
			z_ws, u_ws, sl_ws = z_opt, u_opt, sl_opt

			mpc_path_msg = mpc_path()
			mpc_path_msg.header.stamp = rostm
			mpc_path_msg.solv_status  = str(is_opt)
			mpc_path_msg.solv_time = solve_time

			mpc_path_msg.xs   = z_opt[:,0] 	# x_mpc
			mpc_path_msg.ys   = z_opt[:,1] 	# y_mpc
			mpc_path_msg.psis = z_opt[:,2] 	# psi_mpc	
			mpc_path_msg.vs   = z_opt[:,3]	# v_mpc

			mpc_path_msg.xr   = z_ref[:,0] 	# x_ref
			mpc_path_msg.yr   = z_ref[:,1] 	# y_ref
			mpc_path_msg.psir = z_ref[:,2] 	# psi_ref
			mpc_path_msg.vr   = z_ref[:,3]  # v_ref
			
			mpc_path_msg.df   = u_opt[:,0]	# d_f
			mpc_path_msg.acc  = u_opt[:,1]	# acc

			mpc_path_pub_obj.publish(mpc_path_msg)
		else:
			acc_pub_obj.publish(Float32Msg(-1.0))
			steer_pub_obj.publish(Float32Msg(0.0))						

		loop_rate.sleep()

def start_mpc_node():
	rospy.init_node("dbw_mpc_pf")
	acc_pub   = rospy.Publisher("/control/accel", Float32Msg, queue_size=2)
	steer_pub = rospy.Publisher("/control/steer_angle", Float32Msg, queue_size=2)

	acc_enable_pub   = rospy.Publisher("/control/enable_accel", UInt8Msg, queue_size=2, latch=True)
	steer_enable_pub = rospy.Publisher("/control/enable_spas",  UInt8Msg, queue_size=2, latch=True)

	mpc_path_pub = rospy.Publisher("mpc_path", mpc_path, queue_size=2)
	sub_state  = rospy.Subscriber("state_est", state_est, state_est_callback, queue_size=2)

	acc_enable_pub.publish(UInt8Msg(2))
	steer_enable_pub.publish(UInt8Msg(1))
	pub_loop(acc_pub, steer_pub, mpc_path_pub)

if __name__=='__main__':	
	start_mpc_node()